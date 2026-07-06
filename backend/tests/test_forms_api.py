"""Form upload/get endpoints. Storage (S3) and the Celery enqueue are mocked; FormField
rows for the "filled" tests are seeded directly (as fill_form_task would have written
them) rather than running the real LangGraph pipeline — only route/ownership/PII-safety
logic is under test here."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.core.encryption import build_aad, encrypt_field
from app.models.form import Form, FormField

EMAIL = "citizen@example.com"
PASSWORD = "supersecret1"


def _register_and_login(client, sent_emails, email=EMAIL, password=PASSWORD) -> dict:
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = sent_emails[-1]["token"]
    client.post("/api/auth/verify-email", json={"token": token})
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(autouse=True)
def _mock_storage_and_task(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.forms.put_document", lambda user_id, data, content_type: "forms/fake-key.jpg"
    )
    mock_delay = MagicMock()
    monkeypatch.setattr("app.api.routes.forms.fill_form_task.delay", mock_delay)
    return mock_delay


def _upload(
    client,
    headers,
    form_type="income_certificate",
    content=b"fake-jpeg-bytes",
    content_type="image/jpeg",
    filename="form.jpg",
):
    return client.post(
        "/api/forms/upload",
        headers=headers,
        data={"form_type": form_type},
        files={"file": (filename, content, content_type)},
    )


def test_upload_requires_auth(client):
    r = _upload(client, headers={})
    assert r.status_code == 401


def test_upload_success_enqueues_task(client, sent_emails, _mock_storage_and_task):
    headers = _register_and_login(client, sent_emails)
    r = _upload(client, headers)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    assert body["form_id"]
    _mock_storage_and_task.assert_called_once_with(body["form_id"])


def test_upload_unknown_form_type_rejected(client, sent_emails):
    headers = _register_and_login(client, sent_emails)
    r = _upload(client, headers, form_type="passport_renewal")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "UNKNOWN_FORM_TYPE"


def test_upload_unsupported_content_type_rejected(client, sent_emails):
    headers = _register_and_login(client, sent_emails)
    r = _upload(client, headers, content_type="application/zip", filename="form.zip")
    assert r.status_code == 415
    assert r.json()["detail"]["code"] == "UNSUPPORTED_TYPE"


def test_upload_file_too_large_rejected(client, sent_emails, monkeypatch):
    headers = _register_and_login(client, sent_emails)
    monkeypatch.setattr("app.config.settings.max_upload_bytes", 10)
    r = _upload(client, headers, content=b"x" * 1000)
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "FILE_TOO_LARGE"


def test_upload_enqueue_failure_marks_form_failed(client, sent_emails, monkeypatch):
    headers = _register_and_login(client, sent_emails)
    monkeypatch.setattr(
        "app.api.routes.forms.fill_form_task.delay",
        MagicMock(side_effect=RuntimeError("broker down")),
    )
    r = _upload(client, headers)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "ENQUEUE_FAILED"


def test_get_form_pending_has_no_fields_yet(client, sent_emails):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]

    r = client.get(f"/api/forms/{form_id}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == form_id
    assert body["form_type"] == "income_certificate"
    assert body["display_name"] == "Income Certificate"
    assert body["status"] == "pending"
    assert body["fields"] == []


def test_get_form_unknown_404(client, sent_emails):
    headers = _register_and_login(client, sent_emails)
    r = client.get(f"/api/forms/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404


def test_get_form_cross_user_404(client, sent_emails):
    headers_a = _register_and_login(client, sent_emails, email="a@example.com")
    upload = _upload(client, headers_a)
    form_id = upload.json()["form_id"]

    headers_b = _register_and_login(client, sent_emails, email="b@example.com")
    r = client.get(f"/api/forms/{form_id}", headers=headers_b)
    assert r.status_code == 404


def test_get_filled_form_returns_masked_fields_never_full_aadhaar(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]

    form = db_session.get(Form, uuid.UUID(form_id))
    form.status = "filled"
    form.detected_form_type = "income_certificate"

    aad_name = build_aad(form.id, "applicant_name")
    db_session.add(
        FormField(
            form_id=form.id,
            field_name="applicant_name",
            profile_key="full_name",
            value_encrypted=encrypt_field("Ravi Kumar", aad=aad_name),
            confidence=0.95,
            confidence_band="high",
            high_stakes=False,
            transformed=False,
            needs_review=False,
            reviewed=False,
            flags={"missing": None, "high_stakes": False, "unverified_source": False, "low_confidence": False, "transformed": False},
        )
    )
    aad_aadhaar = build_aad(form.id, "aadhaar_number")
    db_session.add(
        FormField(
            form_id=form.id,
            field_name="aadhaar_number",
            profile_key="aadhaar_number",
            value_encrypted=encrypt_field("234123412346", aad=aad_aadhaar),
            value_masked="XXXX XXXX 2346",
            confidence=0.9,
            confidence_band="high",
            high_stakes=True,
            transformed=False,
            needs_review=True,
            review_reason="high_stakes",
            reviewed=False,
            flags={"missing": None, "high_stakes": True, "unverified_source": False, "low_confidence": False, "transformed": False},
        )
    )
    db_session.add(
        FormField(
            form_id=form.id,
            field_name="annual_income",
            profile_key=None,
            value_encrypted=None,
            confidence=0.0,
            confidence_band="low",
            high_stakes=True,
            transformed=False,
            needs_review=True,
            review_reason="no_mapping",
            reviewed=False,
            flags={"missing": "no_mapping", "high_stakes": True, "unverified_source": False, "low_confidence": True, "transformed": False},
        )
    )
    db_session.commit()

    r = client.get(f"/api/forms/{form_id}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "filled"
    assert "234123412346" not in r.text  # never a full Aadhaar in the response

    fields = {f["field_name"]: f for f in body["fields"]}
    assert fields["applicant_name"]["display_value"] == "Ravi Kumar"
    assert fields["aadhaar_number"]["display_value"] == "XXXX XXXX 2346"
    assert fields["aadhaar_number"]["needs_review"] is True
    assert fields["annual_income"]["display_value"] is None
    assert fields["annual_income"]["review_reason"] == "no_mapping"
