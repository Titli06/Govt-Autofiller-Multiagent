"""GET /api/history — past-forms projection (SPEC-PHASE5.md §6.1, §9).

Form rows are seeded directly (as fill_form_task would have written them) rather
than running the real pipeline — only the history projection/filter/ownership logic
is under test here."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.form import Form, FormField
from app.models.metrics import PipelineRun

EMAIL = "citizen@example.com"
PASSWORD = "supersecret1"


def _register_and_login(client, sent_emails, email=EMAIL, password=PASSWORD) -> tuple[dict, str]:
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = sent_emails[-1]["token"]
    client.post("/api/auth/verify-email", json={"token": token})
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]["id"]


def _make_form(
    db_session,
    user_id,
    *,
    status="approved",
    schema_source="template",
    declared_form_type="income_certificate",
    detected_form_type=None,
    fill_error=None,
    created_at=None,
) -> Form:
    form = Form(
        user_id=uuid.UUID(user_id),
        declared_form_type=declared_form_type,
        detected_form_type=detected_form_type,
        s3_key=f"forms/{user_id}/{uuid.uuid4()}.jpg",
        status=status,
        schema_source=schema_source,
        fill_error=fill_error,
        **({"created_at": created_at} if created_at is not None else {}),
    )
    db_session.add(form)
    db_session.flush()
    return form


def _add_field(db_session, form_id, field_name, *, needs_review=False, reviewed=False) -> FormField:
    field = FormField(
        form_id=form_id,
        field_name=field_name,
        confidence=0.9,
        confidence_band="high",
        high_stakes=False,
        transformed=False,
        needs_review=needs_review,
        reviewed=reviewed,
    )
    db_session.add(field)
    db_session.flush()
    return field


def test_history_empty_when_no_forms(client, sent_emails):
    headers, _ = _register_and_login(client, sent_emails)
    r = client.get("/api/history", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"forms": []}


def test_history_requires_auth(client):
    r = client.get("/api/history")
    assert r.status_code == 401


def test_history_excludes_pending_and_processing(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    _make_form(db_session, user_id, status="pending")
    _make_form(db_session, user_id, status="processing")
    visible = _make_form(db_session, user_id, status="approved")
    db_session.commit()

    r = client.get("/api/history", headers=headers)
    assert r.status_code == 200
    ids = [f["id"] for f in r.json()["forms"]]
    assert ids == [str(visible.id)]


def test_history_includes_failed_and_type_mismatch_with_reason(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    _make_form(db_session, user_id, status="failed", fill_error="could not detect any fields")
    _make_form(db_session, user_id, status="type_mismatch")
    db_session.commit()

    r = client.get("/api/history", headers=headers)
    assert r.status_code == 200
    statuses = {f["status"]: f["fill_error"] for f in r.json()["forms"]}
    assert statuses["failed"] == "could not detect any fields"
    assert statuses["type_mismatch"] is None


def test_history_newest_first(client, sent_emails, db_session):
    # Explicit, distinct created_at — SQLite's CURRENT_TIMESTAMP has only
    # second-level granularity, so two forms created back-to-back in real time
    # could otherwise tie and make ordering flaky.
    headers, user_id = _register_and_login(client, sent_emails)
    base = datetime.now(timezone.utc)
    first = _make_form(db_session, user_id, status="approved", created_at=base)
    second = _make_form(db_session, user_id, status="approved", created_at=base + timedelta(seconds=5))
    db_session.commit()

    r = client.get("/api/history", headers=headers)
    ids = [f["id"] for f in r.json()["forms"]]
    assert ids == [str(second.id), str(first.id)]


def test_history_surfaces_schema_source_and_inferred_display_name(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    inferred = _make_form(
        db_session,
        user_id,
        status="in_review",
        schema_source="inferred",
        declared_form_type="Marriage Certificate",
    )
    db_session.commit()

    r = client.get("/api/history", headers=headers)
    item = next(f for f in r.json()["forms"] if f["id"] == str(inferred.id))
    assert item["schema_source"] == "inferred"
    assert item["display_name"] == "Marriage Certificate"
    assert item["form_type"] == "Marriage Certificate"


def test_history_template_form_resolves_display_name(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    form = _make_form(db_session, user_id, status="approved", declared_form_type="income_certificate")
    db_session.commit()

    r = client.get("/api/history", headers=headers)
    item = next(f for f in r.json()["forms"] if f["id"] == str(form.id))
    assert item["display_name"] == "Income Certificate"


def test_history_field_counts_and_download_ready(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    approved = _make_form(db_session, user_id, status="approved")
    db_session.flush()
    _add_field(db_session, approved.id, "full_name", needs_review=False, reviewed=False)
    _add_field(db_session, approved.id, "dob", needs_review=True, reviewed=True)

    in_review = _make_form(db_session, user_id, status="in_review")
    db_session.flush()
    _add_field(db_session, in_review.id, "father_name", needs_review=True, reviewed=False)
    _add_field(db_session, in_review.id, "address", needs_review=False, reviewed=False)
    db_session.commit()

    r = client.get("/api/history", headers=headers)
    items = {f["id"]: f for f in r.json()["forms"]}

    approved_item = items[str(approved.id)]
    assert approved_item["total_fields"] == 2
    assert approved_item["outstanding_fields"] == 0
    assert approved_item["download_ready"] is True

    review_item = items[str(in_review.id)]
    assert review_item["total_fields"] == 2
    assert review_item["outstanding_fields"] == 1
    assert review_item["download_ready"] is False


def test_history_cross_user_isolation(client, sent_emails, db_session):
    headers_a, user_a = _register_and_login(client, sent_emails, email="a@example.com")
    _, user_b = _register_and_login(client, sent_emails, email="b@example.com")
    _make_form(db_session, user_a, status="approved")
    _make_form(db_session, user_b, status="approved")
    db_session.commit()

    r = client.get("/api/history", headers=headers_a)
    assert len(r.json()["forms"]) == 1


# --- Phase 6: per-form latency (SPEC-PHASE6.md §6.6) -----------------------------------


def test_history_surfaces_latency_from_pipeline_run(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    form = _make_form(db_session, user_id, status="approved")
    db_session.flush()
    db_session.add(
        PipelineRun(
            form_id=form.id,
            user_id=uuid.UUID(user_id),
            schema_source="template",
            terminal_status="approved",
            total_fields=2,
            autofilled_fields=2,
            fill_latency_ms=4200,
            review_latency_ms=0,
        )
    )
    db_session.commit()

    r = client.get("/api/history", headers=headers)
    item = next(f for f in r.json()["forms"] if f["id"] == str(form.id))
    assert item["fill_latency_ms"] == 4200
    assert item["review_latency_ms"] == 0


def test_history_latency_null_for_pre_phase6_form_without_pipeline_run(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    form = _make_form(db_session, user_id, status="approved")
    db_session.commit()

    r = client.get("/api/history", headers=headers)
    item = next(f for f in r.json()["forms"] if f["id"] == str(form.id))
    assert item["fill_latency_ms"] is None
    assert item["review_latency_ms"] is None
