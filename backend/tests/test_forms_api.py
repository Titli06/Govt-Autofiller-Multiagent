"""Form upload/get endpoints, and (Phase 3) the review/download/file endpoints.
Storage (S3) and the Celery enqueue are mocked; FormField rows are seeded directly
(as fill_form_task would have written them) rather than running the real LangGraph
pipeline — only route/ownership/PII-safety/lifecycle logic is under test here. See
test_fill_form_task.py for the pipeline itself and test_form_renderer.py for the real
PyMuPDF rendering path."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.core.encryption import build_aad, decrypt_field, encrypt_field
from app.models.document import Document
from app.models.form import Form, FormField
from app.models.profile import Profile, ProfileField
from app.models.user import User

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
    form.status = "in_review"  # aadhaar_number/annual_income below are still outstanding
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
    assert body["status"] == "in_review"
    assert "234123412346" not in r.text  # never a full Aadhaar in the response

    fields = {f["field_name"]: f for f in body["fields"]}
    assert fields["applicant_name"]["display_value"] == "Ravi Kumar"
    assert fields["aadhaar_number"]["display_value"] == "XXXX XXXX 2346"
    assert fields["aadhaar_number"]["needs_review"] is True
    assert fields["annual_income"]["display_value"] is None
    assert fields["annual_income"]["review_reason"] == "no_mapping"


# --- Phase 3: review projection, review actions, download, file --------------------


def _seed_reviewable_form(db_session, form_id, *, status="in_review"):
    """Seeds a form with one green field (already fine), one flagged-missing field
    (annual_income, no_mapping), and one flagged high-stakes field (aadhaar_number,
    verified) — the minimal shape needed to exercise the review lifecycle."""
    form = db_session.get(Form, uuid.UUID(form_id))
    form.status = status
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
            verified=True,
            verification_method="exact",
            needs_review=False,
            reviewed=False,
            flags={"missing": None, "verification_failed": False, "high_stakes": False, "unverified_source": False, "low_confidence": False, "transformed": False},
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
            confidence=0.95,
            confidence_band="high",
            high_stakes=True,
            transformed=False,
            verified=True,
            verification_method="exact",
            needs_review=True,
            review_reason="high_stakes",
            reviewed=False,
            flags={"missing": None, "verification_failed": False, "high_stakes": True, "unverified_source": False, "low_confidence": False, "transformed": False},
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
            verified=False,
            needs_review=True,
            review_reason="no_mapping",
            reviewed=False,
            flags={"missing": "no_mapping", "verification_failed": False, "high_stakes": True, "unverified_source": False, "low_confidence": True, "transformed": False},
        )
    )
    db_session.commit()
    rows = {f.field_name: f for f in db_session.query(FormField).filter_by(form_id=form.id).all()}
    return form, rows


def test_get_review_requires_auth(client):
    r = client.get(f"/api/forms/{uuid.uuid4()}/review")
    assert r.status_code == 401


def test_get_review_cross_user_404(client, sent_emails):
    headers_a = _register_and_login(client, sent_emails, email="a2@example.com")
    upload = _upload(client, headers_a)
    form_id = upload.json()["form_id"]

    headers_b = _register_and_login(client, sent_emails, email="b2@example.com")
    r = client.get(f"/api/forms/{form_id}/review", headers=headers_b)
    assert r.status_code == 404


def test_get_review_lists_fields_and_counts(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    _seed_reviewable_form(db_session, form_id)

    r = client.get(f"/api/forms/{form_id}/review", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_review"
    assert body["download_ready"] is False
    assert body["total_fields"] == 3
    assert body["outstanding_fields"] == 2
    by_name = {f["field_name"]: f for f in body["fields"]}
    assert by_name["applicant_name"]["outstanding"] is False
    assert by_name["aadhaar_number"]["outstanding"] is True
    assert by_name["aadhaar_number"]["verified"] is True
    assert "234123412346" not in r.text


def test_get_review_surfaces_placement_warning(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    form, _ = _seed_reviewable_form(db_session, form_id)
    form.placement_warning = "This scan looks rotated ~12°; re-scan upright for best results."
    db_session.commit()

    r = client.get(f"/api/forms/{form_id}/review", headers=headers)
    assert r.json()["placement_warning"].startswith("This scan looks rotated")


def test_review_approve_resolves_field(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    _, rows = _seed_reviewable_form(db_session, form_id)

    r = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={"field_id": str(rows["aadhaar_number"].id), "action": "approve"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["field"]["reviewed"] is True
    assert body["field"]["review_action"] == "approved"
    assert body["status"] == "in_review"  # annual_income still outstanding
    assert body["download_ready"] is False


def test_review_resolving_last_outstanding_field_approves_form(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    _, rows = _seed_reviewable_form(db_session, form_id)

    client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={"field_id": str(rows["aadhaar_number"].id), "action": "approve"},
    )
    r = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={"field_id": str(rows["annual_income"].id), "action": "approve_blank"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["download_ready"] is True


def test_review_approve_blank_rejected_when_field_has_a_value(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    _, rows = _seed_reviewable_form(db_session, form_id)

    r = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={"field_id": str(rows["aadhaar_number"].id), "action": "approve_blank"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NOT_BLANK"


def test_review_correct_validates_dob_format(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    form, _ = _seed_reviewable_form(db_session, form_id)
    aad = build_aad(form.id, "date_of_birth")
    dob_field = FormField(
        form_id=form.id,
        field_name="date_of_birth",
        profile_key="dob",
        value_encrypted=encrypt_field("12/04/1998", aad=aad),
        confidence=0.5,
        confidence_band="low",
        high_stakes=True,
        transformed=True,
        verified=False,
        verification_method="llm",
        needs_review=True,
        review_reason="verification_failed",
        reviewed=False,
        flags={"missing": None, "verification_failed": True, "high_stakes": True, "unverified_source": False, "low_confidence": True, "transformed": True},
    )
    db_session.add(dob_field)
    db_session.commit()

    bad = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={"field_id": str(dob_field.id), "action": "correct", "value": "not-a-date"},
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "INVALID_VALUE"

    good = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={"field_id": str(dob_field.id), "action": "correct", "value": "13/04/1998"},
    )
    assert good.status_code == 200
    field_out = good.json()["field"]
    assert field_out["verified"] is True
    assert field_out["verification_method"] == "user"
    assert field_out["confidence"] == 1.0
    assert field_out["reviewed"] is True


def test_review_correct_propagates_to_existing_profile_candidate(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    form, _ = _seed_reviewable_form(db_session, form_id)

    user = db_session.query(User).filter_by(email=EMAIL).one()
    profile = Profile(user_id=user.id)
    db_session.add(profile)
    db_session.flush()
    doc = Document(user_id=user.id, declared_doc_type="aadhaar", s3_key="d/x.jpg", ocr_status="extracted")
    db_session.add(doc)
    db_session.flush()
    aad_pf = build_aad(profile.id, "full_name")
    pf = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc.id,
        field_name="full_name",
        value_encrypted=encrypt_field("Ravi Kumr", aad=aad_pf),
        confidence=0.6,
        confidence_band="medium",
        high_stakes=False,
        status="needs_confirmation",
    )
    db_session.add(pf)
    db_session.flush()

    name_field = db_session.query(FormField).filter_by(form_id=form.id, field_name="applicant_name").one()
    name_field.profile_field_id = pf.id
    db_session.commit()

    r = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={
            "field_id": str(name_field.id),
            "action": "correct",
            "value": "Ravi Kumar",
            "propagate_to_profile": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["warning"] is None

    db_session.refresh(pf)
    assert decrypt_field(pf.effective_value_encrypted, aad=aad_pf) == "Ravi Kumar"
    assert pf.status == "user_corrected"


def test_review_correct_missing_field_with_propagate_creates_manual_candidate(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    form, _ = _seed_reviewable_form(db_session, form_id)
    father_field = FormField(
        form_id=form.id,
        field_name="father_name",
        profile_key="father_name",
        value_encrypted=None,
        confidence=0.0,
        confidence_band="low",
        high_stakes=False,
        transformed=False,
        needs_review=True,
        review_reason="no_candidate",
        reviewed=False,
        flags={"missing": "no_candidate", "verification_failed": False, "high_stakes": False, "unverified_source": False, "low_confidence": True, "transformed": False},
    )
    db_session.add(father_field)
    db_session.commit()

    r = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={
            "field_id": str(father_field.id),
            "action": "correct",
            "value": "Suresh Kumar",
            "propagate_to_profile": True,
        },
    )
    assert r.status_code == 200

    user = db_session.query(User).filter_by(email=EMAIL).one()
    profile = db_session.query(Profile).filter_by(user_id=user.id).one()
    manual = (
        db_session.query(ProfileField)
        .filter_by(profile_id=profile.id, field_name="father_name", origin="manual")
        .one()
    )
    assert manual.source_doc_id is None
    aad_pf = build_aad(profile.id, "father_name")
    assert decrypt_field(manual.effective_value_encrypted, aad=aad_pf) == "Suresh Kumar"


def test_review_correct_no_mapping_field_propagate_is_a_noop_with_warning(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    _, rows = _seed_reviewable_form(db_session, form_id)

    r = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={
            "field_id": str(rows["annual_income"].id),
            "action": "correct",
            "value": "500000",
            "propagate_to_profile": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["warning"] is not None


def test_review_action_cross_user_404(client, sent_emails, db_session):
    headers_a = _register_and_login(client, sent_emails, email="ra@example.com")
    upload = _upload(client, headers_a)
    form_id = upload.json()["form_id"]
    _, rows = _seed_reviewable_form(db_session, form_id)

    headers_b = _register_and_login(client, sent_emails, email="rb@example.com")
    r = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers_b,
        json={"field_id": str(rows["aadhaar_number"].id), "action": "approve"},
    )
    assert r.status_code == 404


def test_review_correct_on_approved_form_reopens_field_and_invalidates_cache(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    form, rows = _seed_reviewable_form(db_session, form_id, status="approved")
    for f in rows.values():
        f.reviewed = True
        f.review_action = "approved"
    form.rendered_s3_key = "forms/cached.pdf"
    db_session.commit()

    r = client.post(
        f"/api/forms/{form_id}/review",
        headers=headers,
        json={"field_id": str(rows["aadhaar_number"].id), "action": "correct", "value": "234123412346"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_review"
    assert body["download_ready"] is False
    assert body["field"]["reviewed"] is False  # re-opened, must be explicitly re-approved

    db_session.refresh(form)
    assert form.rendered_s3_key is None


def test_download_blocked_until_approved(client, sent_emails, db_session):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    _seed_reviewable_form(db_session, form_id)

    r = client.get(f"/api/forms/{form_id}/download", headers=headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "REVIEW_INCOMPLETE"


def test_download_cross_user_404(client, sent_emails, db_session):
    headers_a = _register_and_login(client, sent_emails, email="da@example.com")
    upload = _upload(client, headers_a)
    form_id = upload.json()["form_id"]

    headers_b = _register_and_login(client, sent_emails, email="db@example.com")
    r = client.get(f"/api/forms/{form_id}/download", headers=headers_b)
    assert r.status_code == 404


def test_download_renders_caches_and_streams_pdf(client, sent_emails, db_session, monkeypatch):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]
    form, _ = _seed_reviewable_form(db_session, form_id, status="approved")
    for f in db_session.query(FormField).filter_by(form_id=form.id).all():
        f.reviewed = True
    db_session.commit()

    render_calls = []

    def fake_render(form_type, fields, blank_bytes, content_type):
        render_calls.append(form_type)
        return b"%PDF-fake-bytes%"

    monkeypatch.setattr("app.api.routes.forms.get_document", lambda key: b"blank-bytes")
    monkeypatch.setattr("app.api.routes.forms.put_document", lambda user_id, data, ct: "forms/rendered.pdf")
    monkeypatch.setattr("app.api.routes.forms.render", fake_render)

    r1 = client.get(f"/api/forms/{form_id}/download", headers=headers)
    assert r1.status_code == 200
    assert r1.headers["content-type"] == "application/pdf"
    assert "income_certificate.pdf" in r1.headers["content-disposition"]
    assert r1.content == b"%PDF-fake-bytes%"

    r2 = client.get(f"/api/forms/{form_id}/download", headers=headers)
    assert r2.status_code == 200
    assert len(render_calls) == 1  # second download reuses the cached render

    db_session.refresh(form)
    assert form.rendered_s3_key == "forms/rendered.pdf"


def test_get_form_file_serves_blank_form_bytes(client, sent_emails, monkeypatch):
    headers = _register_and_login(client, sent_emails)
    monkeypatch.setattr("app.api.routes.forms.get_document", lambda key: b"blank-form-bytes")
    upload = _upload(client, headers)
    form_id = upload.json()["form_id"]

    r = client.get(f"/api/forms/{form_id}/file", headers=headers)
    assert r.status_code == 200
    assert r.content == b"blank-form-bytes"
    assert r.headers["content-type"] == "image/jpeg"


def test_get_form_file_cross_user_404(client, sent_emails):
    headers_a = _register_and_login(client, sent_emails, email="fa@example.com")
    upload = _upload(client, headers_a)
    form_id = upload.json()["form_id"]

    headers_b = _register_and_login(client, sent_emails, email="fb@example.com")
    r = client.get(f"/api/forms/{form_id}/file", headers=headers_b)
    assert r.status_code == 404
