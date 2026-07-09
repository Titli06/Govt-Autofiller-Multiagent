"""Profile view + confirm/correct endpoints, and (Phase 5) the data-only purge.
ProfileField/Document/Form rows are seeded directly (as ocr_extract_task/
fill_form_task would have written them) rather than running real pipelines."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.core.encryption import build_aad, encrypt_field
from app.models.document import Document
from app.models.form import Form, FormField
from app.models.metrics import PipelineRun
from app.models.profile import Profile, ProfileField
from app.models.refresh_token import RefreshToken
from app.models.user import User

EMAIL = "citizen@example.com"
PASSWORD = "supersecret1"


def _register_and_login(client, sent_emails, email=EMAIL, password=PASSWORD) -> dict:
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = sent_emails[-1]["token"]
    client.post("/api/auth/verify-email", json={"token": token})
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]["id"]


def _seed_field(db_session, user_id, field_name, value, *, high_stakes=False, status="confirmed", confidence=0.95, band="high"):
    profile = db_session.query(Profile).filter_by(user_id=uuid.UUID(user_id)).one_or_none()
    if profile is None:
        profile = Profile(user_id=uuid.UUID(user_id))
        db_session.add(profile)
        db_session.flush()

    doc = Document(
        user_id=uuid.UUID(user_id),
        declared_doc_type="aadhaar",
        s3_key="documents/x/y.jpg",
        ocr_status="extracted",
    )
    db_session.add(doc)
    db_session.flush()

    aad = build_aad(profile.id, field_name)
    field = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc.id,
        field_name=field_name,
        value_encrypted=encrypt_field(value, aad=aad),
        confidence=confidence,
        confidence_band=band,
        high_stakes=high_stakes,
        status=status,
        validators={"snippet_contains": True, "format_valid": True, "normalized": False},
    )
    if field_name in ("aadhaar_number", "pan_number"):
        from app.core.encryption import mask_for

        field.value_masked = mask_for(field_name, value)
    db_session.add(field)
    db_session.commit()
    return field


def test_get_profile_empty_when_no_documents(client, sent_emails):
    headers, _ = _register_and_login(client, sent_emails)
    r = client.get("/api/profile", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"fields": []}


def test_get_profile_returns_masked_and_plaintext_values(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    _seed_field(db_session, user_id, "full_name", "Rajesh Kumar", confidence=0.85, band="medium")
    _seed_field(
        db_session,
        user_id,
        "aadhaar_number",
        "234123412346",
        high_stakes=True,
        status="needs_confirmation",
        confidence=0.96,
    )

    r = client.get("/api/profile", headers=headers)
    assert r.status_code == 200
    fields = {f["field_name"]: f for f in r.json()["fields"]}

    assert fields["full_name"]["display_value"] == "Rajesh Kumar"
    assert fields["aadhaar_number"]["display_value"] == "XXXX XXXX 2346"
    assert "234123412346" not in r.text  # never a full Aadhaar number in the response
    assert fields["aadhaar_number"]["high_stakes"] is True
    assert fields["aadhaar_number"]["status"] == "needs_confirmation"
    assert fields["full_name"]["source"]["doc_type"] == "aadhaar"


def test_get_profile_requires_auth(client):
    r = client.get("/api/profile")
    assert r.status_code == 401


def test_confirm_field_transitions_status(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    field = _seed_field(db_session, user_id, "full_name", "Rajesh Kumar", status="needs_confirmation")

    r = client.post(f"/api/profile/fields/{field.id}/confirm", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "user_confirmed"


def test_confirm_field_cross_user_404(client, sent_emails, db_session):
    _, user_a_id = _register_and_login(client, sent_emails, email="a@example.com")
    field = _seed_field(db_session, user_a_id, "full_name", "Rajesh Kumar")

    headers_b, _ = _register_and_login(client, sent_emails, email="b@example.com")
    r = client.post(f"/api/profile/fields/{field.id}/confirm", headers=headers_b)
    assert r.status_code == 404


def test_confirm_unknown_field_404(client, sent_emails):
    headers, _ = _register_and_login(client, sent_emails)
    r = client.post(
        f"/api/profile/fields/{uuid.uuid4()}/confirm", headers=headers
    )
    assert r.status_code == 404


def test_correct_field_updates_value_and_confidence(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    field = _seed_field(
        db_session,
        user_id,
        "aadhaar_number",
        "123456789012",  # deliberately wrong / low-confidence seed
        high_stakes=True,
        status="needs_confirmation",
        confidence=0.4,
        band="low",
    )

    r = client.post(
        f"/api/profile/fields/{field.id}/correct",
        headers=headers,
        json={"value": "234123412346"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "user_corrected"
    assert body["confidence"] == 1.0
    assert body["display_value"] == "XXXX XXXX 2346"


def test_correct_field_rejects_invalid_format(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    field = _seed_field(db_session, user_id, "pan_number", "ABCDE1234F", high_stakes=True)

    r = client.post(
        f"/api/profile/fields/{field.id}/correct",
        headers=headers,
        json={"value": "not-a-pan"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_VALUE"


def test_correct_field_free_text_no_format_rule(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    field = _seed_field(db_session, user_id, "address", "123 MG Road")

    r = client.post(
        f"/api/profile/fields/{field.id}/correct",
        headers=headers,
        json={"value": "456 Park Street"},
    )
    assert r.status_code == 200
    assert r.json()["display_value"] == "456 Park Street"


# --- Phase 5: DELETE /api/profile (data-only purge, SPEC-PHASE5.md §6.2/§9) ----------


@pytest.fixture()
def deleted_s3_keys(monkeypatch) -> list[str]:
    """Captures every key passed to delete_document instead of hitting real S3."""
    calls: list[str] = []
    monkeypatch.setattr("app.api.routes.profile.delete_document", lambda key: calls.append(key))
    return calls


def _seed_document(db_session, user_id, *, ocr_status="extracted", s3_key=None, updated_at=None) -> Document:
    doc = Document(
        user_id=uuid.UUID(user_id),
        declared_doc_type="aadhaar",
        s3_key=s3_key or f"documents/{user_id}/{uuid.uuid4()}.jpg",
        ocr_status=ocr_status,
        **({"updated_at": updated_at} if updated_at is not None else {}),
    )
    db_session.add(doc)
    db_session.flush()
    return doc


def _seed_form(
    db_session, user_id, *, status="approved", s3_key=None, rendered_s3_key=None, updated_at=None
) -> Form:
    form = Form(
        user_id=uuid.UUID(user_id),
        declared_form_type="income_certificate",
        s3_key=s3_key or f"forms/{user_id}/{uuid.uuid4()}.jpg",
        rendered_s3_key=rendered_s3_key,
        status=status,
        **({"updated_at": updated_at} if updated_at is not None else {}),
    )
    db_session.add(form)
    db_session.flush()
    return form


def test_delete_profile_requires_auth(client):
    r = client.request("DELETE", "/api/profile", json={"password": "x"})
    assert r.status_code == 401


def test_delete_profile_wrong_password_403_nothing_deleted(client, sent_emails, db_session, deleted_s3_keys):
    headers, user_id = _register_and_login(client, sent_emails)
    _seed_field(db_session, user_id, "full_name", "Rajesh Kumar")
    db_session.commit()

    r = client.request(
        "DELETE", "/api/profile", json={"password": "totally-wrong"}, headers=headers
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "INVALID_PASSWORD"
    assert deleted_s3_keys == []
    assert db_session.query(Profile).filter_by(user_id=uuid.UUID(user_id)).count() == 1
    assert db_session.query(ProfileField).count() == 1


def test_delete_profile_idempotent_on_empty_account(client, sent_emails, deleted_s3_keys):
    headers, _ = _register_and_login(client, sent_emails)
    r = client.request("DELETE", "/api/profile", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 200
    assert r.json() == {
        "documents_deleted": 0,
        "forms_deleted": 0,
        "profile_fields_deleted": 0,
        "s3_objects_deleted": 0,
        "s3_delete_failures": 0,
    }
    assert deleted_s3_keys == []


def test_delete_profile_happy_path_purges_everything_and_account_survives(
    client, sent_emails, db_session, deleted_s3_keys
):
    headers, user_id = _register_and_login(client, sent_emails)

    # A document-origin candidate...
    field = _seed_field(db_session, user_id, "full_name", "Rajesh Kumar")
    # ...and a manual-origin candidate (Phase 3 Decision 11: no source_doc_id) — must
    # cascade too, no special-casing (SPEC-PHASE5.md §8).
    manual = ProfileField(
        profile_id=field.profile_id,
        source_doc_id=None,
        origin="manual",
        field_name="father_name",
        value_encrypted=encrypt_field(
            "Ramesh Kumar", aad=build_aad(field.profile_id, "father_name")
        ),
        confidence=1.0,
        confidence_band="high",
        high_stakes=False,
        status="user_corrected",
    )
    db_session.add(manual)
    db_session.commit()

    form = _seed_form(
        db_session, user_id, status="approved", rendered_s3_key=f"forms/{user_id}/rendered.pdf"
    )
    db_session.flush()
    ff = FormField(
        form_id=form.id,
        field_name="applicant_name",
        confidence=0.9,
        confidence_band="high",
        high_stakes=False,
        transformed=False,
        needs_review=False,
        reviewed=True,
    )
    db_session.add(ff)
    db_session.commit()

    # Capture everything needed post-purge BEFORE the request — db_session's own
    # commit() above expired these instances, and re-accessing an expired attribute
    # after another session deletes the row raises ObjectDeletedError rather than
    # just returning stale data.
    doc_s3_key = db_session.get(Document, field.source_doc_id).s3_key
    form_id = form.id
    form_s3_key = form.s3_key
    form_rendered_s3_key = form.rendered_s3_key

    r = client.request("DELETE", "/api/profile", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["documents_deleted"] == 1
    assert body["forms_deleted"] == 1
    assert body["profile_fields_deleted"] == 2  # full_name (document) + father_name (manual)
    assert body["s3_objects_deleted"] == 3  # document s3_key + form s3_key + rendered_s3_key
    assert body["s3_delete_failures"] == 0
    assert set(deleted_s3_keys) == {doc_s3_key, form_s3_key, form_rendered_s3_key}

    assert db_session.query(Profile).filter_by(user_id=uuid.UUID(user_id)).count() == 0
    assert db_session.query(ProfileField).count() == 0
    assert db_session.query(Document).filter_by(user_id=uuid.UUID(user_id)).count() == 0
    assert db_session.query(Form).filter_by(user_id=uuid.UUID(user_id)).count() == 0
    assert db_session.query(FormField).filter_by(form_id=form_id).count() == 0

    # Account survives (Decision 1): the User row and its session stay intact.
    assert db_session.get(User, uuid.UUID(user_id)) is not None
    assert db_session.query(RefreshToken).filter_by(user_id=uuid.UUID(user_id)).count() >= 1

    # Still authenticated with the same token after the purge, on an empty profile.
    r2 = client.get("/api/profile", headers=headers)
    assert r2.status_code == 200
    assert r2.json() == {"fields": []}


def test_delete_profile_blocked_while_recent_processing_document(
    client, sent_emails, db_session, deleted_s3_keys
):
    headers, user_id = _register_and_login(client, sent_emails)
    _seed_document(db_session, user_id, ocr_status="processing")
    db_session.commit()

    r = client.request("DELETE", "/api/profile", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "JOBS_IN_PROGRESS"
    assert deleted_s3_keys == []
    assert db_session.query(Document).filter_by(user_id=uuid.UUID(user_id)).count() == 1


def test_delete_profile_blocked_while_recent_processing_form(
    client, sent_emails, db_session, deleted_s3_keys
):
    headers, user_id = _register_and_login(client, sent_emails)
    _seed_form(db_session, user_id, status="processing")
    db_session.commit()

    r = client.request("DELETE", "/api/profile", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "JOBS_IN_PROGRESS"


def test_delete_profile_stale_processing_job_does_not_block(
    client, sent_emails, db_session, monkeypatch, deleted_s3_keys
):
    monkeypatch.setattr("app.config.settings.purge_stale_job_seconds", 60)
    headers, user_id = _register_and_login(client, sent_emails)
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    _seed_document(db_session, user_id, ocr_status="processing", updated_at=stale)
    db_session.commit()

    r = client.request("DELETE", "/api/profile", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 200
    assert r.json()["documents_deleted"] == 1


def test_delete_profile_s3_failure_still_commits_db(client, sent_emails, db_session, monkeypatch):
    headers, user_id = _register_and_login(client, sent_emails)
    _seed_field(db_session, user_id, "full_name", "Rajesh Kumar")
    db_session.commit()

    monkeypatch.setattr(
        "app.api.routes.profile.delete_document",
        MagicMock(side_effect=RuntimeError("s3 unreachable")),
    )

    r = client.request("DELETE", "/api/profile", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["s3_delete_failures"] >= 1
    assert body["s3_objects_deleted"] == 0
    assert body["documents_deleted"] == 1
    assert db_session.query(Document).filter_by(user_id=uuid.UUID(user_id)).count() == 0
    assert db_session.query(Profile).filter_by(user_id=uuid.UUID(user_id)).count() == 0


def test_delete_profile_cross_user_isolation(client, sent_emails, db_session, deleted_s3_keys):
    headers_a, user_a = _register_and_login(client, sent_emails, email="a@example.com")
    _, user_b = _register_and_login(client, sent_emails, email="b@example.com")
    _seed_field(db_session, user_a, "full_name", "A Name")
    field_b = _seed_field(db_session, user_b, "full_name", "B Name")
    doc_b = _seed_document(db_session, user_b)
    form_b = _seed_form(db_session, user_b)
    db_session.commit()

    r = client.request("DELETE", "/api/profile", json={"password": PASSWORD}, headers=headers_a)
    assert r.status_code == 200

    assert db_session.query(ProfileField).filter_by(id=field_b.id).count() == 1
    assert db_session.query(Profile).filter_by(user_id=uuid.UUID(user_b)).count() == 1
    assert db_session.query(Document).filter_by(id=doc_b.id).count() == 1
    assert db_session.query(Form).filter_by(id=form_b.id).count() == 1


# --- Phase 6: purge also deletes pipeline_run rows (SPEC-PHASE6.md §6.8) --------------


def _seed_pipeline_run(db_session, user_id, form) -> PipelineRun:
    run = PipelineRun(
        form_id=form.id,
        user_id=uuid.UUID(user_id),
        schema_source="template",
        terminal_status="approved",
        total_fields=1,
        autofilled_fields=1,
    )
    db_session.add(run)
    db_session.flush()
    return run


def test_delete_profile_removes_pipeline_run_rows(client, sent_emails, db_session, deleted_s3_keys):
    headers, user_id = _register_and_login(client, sent_emails)
    form = _seed_form(db_session, user_id, status="approved")
    _seed_pipeline_run(db_session, user_id, form)
    db_session.commit()

    r = client.request("DELETE", "/api/profile", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 200
    assert db_session.query(PipelineRun).filter_by(user_id=uuid.UUID(user_id)).count() == 0


def test_delete_profile_pipeline_run_cross_user_isolation(client, sent_emails, db_session, deleted_s3_keys):
    headers_a, user_a = _register_and_login(client, sent_emails, email="pa@example.com")
    _, user_b = _register_and_login(client, sent_emails, email="pb@example.com")
    form_b = _seed_form(db_session, user_b, status="approved")
    run_b = _seed_pipeline_run(db_session, user_b, form_b)
    db_session.commit()

    r = client.request("DELETE", "/api/profile", json={"password": PASSWORD}, headers=headers_a)
    assert r.status_code == 200
    assert db_session.query(PipelineRun).filter_by(id=run_b.id).count() == 1
