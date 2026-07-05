"""Profile view + confirm/correct endpoints. ProfileField rows are seeded directly (as
ocr_extract_task would have written them) rather than running a real extraction."""

from __future__ import annotations

import uuid

from app.core.encryption import build_aad, encrypt_field
from app.models.document import Document
from app.models.profile import Profile, ProfileField

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
