"""GET /api/metrics — per-user aggregate metrics (SPEC-PHASE6.md §6.4, §9).

pipeline_run/form_fields/documents rows are seeded directly (as fill_form_task,
the review endpoint, and ocr_extract_task would have written them) rather than
running real pipelines — only the aggregation/formula/scoping logic is under test.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.core.encryption import build_aad, encrypt_field
from app.models.document import Document
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


def _make_form(db_session, user_id, *, schema_source="template", status="approved") -> Form:
    form = Form(
        user_id=uuid.UUID(user_id),
        declared_form_type="income_certificate",
        s3_key=f"forms/{user_id}/{uuid.uuid4()}.jpg",
        status=status,
        schema_source=schema_source,
    )
    db_session.add(form)
    db_session.flush()
    return form


def _make_run(db_session, user_id, form, **overrides) -> PipelineRun:
    defaults = dict(
        form_id=form.id,
        user_id=uuid.UUID(user_id),
        schema_source=form.schema_source,
        terminal_status=form.status,
        total_fields=0,
        autofilled_fields=0,
    )
    defaults.update(overrides)
    run = PipelineRun(**defaults)
    db_session.add(run)
    db_session.flush()
    return run


def _add_field(
    db_session,
    form_id,
    field_name,
    *,
    confidence_band="high",
    mapping_tier=None,
    verified=False,
    has_value=True,
) -> FormField:
    value_encrypted = None
    if has_value:
        aad = build_aad(form_id, field_name)
        value_encrypted = encrypt_field("some value", aad=aad)
    field = FormField(
        form_id=form_id,
        field_name=field_name,
        value_encrypted=value_encrypted,
        confidence=0.9,
        confidence_band=confidence_band,
        mapping_tier=mapping_tier,
        verified=verified,
        high_stakes=False,
        transformed=False,
        needs_review=False,
        reviewed=False,
    )
    db_session.add(field)
    db_session.flush()
    return field


def test_metrics_requires_auth(client):
    r = client.get("/api/metrics")
    assert r.status_code == 401


def test_metrics_empty_account_all_zero_all_null(client, sent_emails):
    headers, _ = _register_and_login(client, sent_emails)
    r = client.get("/api/metrics", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["forms_total"] == 0
    assert body["forms_by_status"] == {}
    assert body["avg_fill_latency_ms"] is None
    assert body["avg_review_latency_ms"] is None
    assert body["avg_ocr_latency_ms"] is None
    assert body["total_fields"] == 0
    assert body["autofill_rate"] is None
    assert body["high_confidence_rate"] is None
    assert body["inferred_forms_total"] == 0
    assert body["schema_inference_success_rate"] is None
    assert body["mapping_tier_distribution"] == {}
    assert body["verification_pass_rate"] is None
    assert body["accuracy_proxy"] is None
    assert body["estimated_manual_seconds"] == 0
    assert body["measured_review_seconds"] == 0
    assert body["estimated_time_saved_seconds"] == 0
    assert body["forms_per_profile"] == 0


def test_metrics_aggregates_over_seeded_mix(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)

    # Template form, approved, 2 fields (1 auto-filled, 1 not) — contributes latency +
    # accuracy-proxy counts.
    tpl = _make_form(db_session, user_id, schema_source="template", status="approved")
    _make_run(
        db_session,
        user_id,
        tpl,
        total_fields=2,
        autofilled_fields=1,
        fill_latency_ms=1000,
        review_latency_ms=2000,
        reviewed_fields=1,
        approved_as_is=1,
        corrected_fields=0,
    )
    _add_field(db_session, tpl.id, "full_name", confidence_band="high", verified=True)
    _add_field(db_session, tpl.id, "address", confidence_band="medium", verified=False)

    # Inferred form, in_review (success), 1 field with a weak tier.
    inf_success = _make_form(db_session, user_id, schema_source="inferred", status="in_review")
    _make_run(
        db_session,
        user_id,
        inf_success,
        total_fields=1,
        autofilled_fields=0,
        fill_latency_ms=3000,
    )
    _add_field(db_session, inf_success.id, "father_name", confidence_band="low", mapping_tier="weak")

    # Inferred form, failed (schema-inference failure) — zero fields.
    inf_failed = _make_form(db_session, user_id, schema_source="inferred", status="failed")
    _make_run(db_session, user_id, inf_failed, total_fields=0, autofilled_fields=0)

    # A form with one corrected field, to exercise the accuracy proxy denominator.
    corrected_form = _make_form(db_session, user_id, schema_source="template", status="approved")
    _make_run(
        db_session,
        user_id,
        corrected_form,
        total_fields=1,
        autofilled_fields=1,
        fill_latency_ms=500,
        review_latency_ms=1000,
        reviewed_fields=1,
        approved_as_is=0,
        corrected_fields=1,
    )
    db_session.commit()

    r = client.get("/api/metrics", headers=headers)
    assert r.status_code == 200
    body = r.json()

    assert body["forms_total"] == 4
    assert body["forms_by_status"] == {"approved": 2, "in_review": 1, "failed": 1}

    assert body["avg_fill_latency_ms"] == (1000 + 3000 + 500) / 3
    assert body["avg_review_latency_ms"] == (2000 + 1000) / 2

    assert body["total_fields"] == 2 + 1 + 0 + 1
    assert body["autofilled_fields"] == 1 + 0 + 0 + 1
    assert body["autofill_rate"] == 2 / 4

    assert body["inferred_forms_total"] == 2
    assert body["schema_inference_success_rate"] == 1 / 2  # in_review counts, failed doesn't

    assert body["mapping_tier_distribution"] == {"weak": 1}

    # 3 form_fields total across tpl+inf_success (corrected_form has none seeded);
    # 2 have a value, 1 (father_name) has a value too (has_value default True) -> 3 with value.
    assert body["high_confidence_rate"] == 1 / 3  # only full_name is "high"
    assert body["verification_pass_rate"] == 1 / 3  # only full_name is verified

    assert body["accuracy_proxy"] == 1 / 2  # approved_as_is=1 vs corrected=1

    assert body["manual_seconds_per_field"] == 45
    assert body["estimated_manual_seconds"] == 4 * 45
    assert body["measured_review_seconds"] == (2000 + 1000) // 1000
    assert body["estimated_time_saved_seconds"] == (4 * 45) - ((2000 + 1000) // 1000)

    assert body["forms_per_profile"] == 4


def test_metrics_cross_user_isolation(client, sent_emails, db_session):
    headers_a, user_a = _register_and_login(client, sent_emails, email="a@example.com")
    _, user_b = _register_and_login(client, sent_emails, email="b@example.com")

    form_b = _make_form(db_session, user_b, status="approved")
    _make_run(db_session, user_b, form_b, total_fields=5, autofilled_fields=5, fill_latency_ms=9999)
    _add_field(db_session, form_b.id, "full_name", confidence_band="high", verified=True)
    db_session.commit()

    r = client.get("/api/metrics", headers=headers_a)
    assert r.status_code == 200
    body = r.json()
    assert body["forms_total"] == 0
    assert body["total_fields"] == 0
    assert body["avg_fill_latency_ms"] is None


def test_metrics_no_pii_in_response(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    form = _make_form(db_session, user_id, status="approved")
    _make_run(db_session, user_id, form, total_fields=1, autofilled_fields=1)
    _add_field(db_session, form.id, "full_name", confidence_band="high", verified=True)
    db_session.commit()

    r = client.get("/api/metrics", headers=headers)
    assert r.status_code == 200
    assert "full_name" not in r.text
    assert "some value" not in r.text


def test_metrics_ocr_latency_from_documents(client, sent_emails, db_session):
    headers, user_id = _register_and_login(client, sent_emails)
    base = datetime.now(timezone.utc)
    doc = Document(
        user_id=uuid.UUID(user_id),
        declared_doc_type="aadhaar",
        s3_key="documents/x/y.jpg",
        ocr_status="extracted",
        created_at=base,
        extracted_at=base + timedelta(seconds=6),
    )
    db_session.add(doc)
    # A still-processing document (no extracted_at) must not count toward the average.
    db_session.add(
        Document(
            user_id=uuid.UUID(user_id),
            declared_doc_type="pan",
            s3_key="documents/x/z.jpg",
            ocr_status="processing",
        )
    )
    db_session.commit()

    r = client.get("/api/metrics", headers=headers)
    assert r.status_code == 200
    assert r.json()["avg_ocr_latency_ms"] == 6000
