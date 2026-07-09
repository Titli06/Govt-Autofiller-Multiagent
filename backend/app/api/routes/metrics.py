"""GET /api/metrics — per-user aggregate metrics (Phase 6, PRD §9, SPEC-PHASE6.md §6.4).

Pure DB arithmetic over `pipeline_run` + `form_fields`/`documents` metadata — no
decryption, no field values, no PII in the response (counts/averages/ratios only,
mirroring History's posture). Strictly per-user (Decision 7); no cross-user or global
aggregate endpoint exists. Ratios are `null` (not `0`) when their denominator is 0.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.models.document import Document
from app.models.form import Form, FormField
from app.models.metrics import PipelineRun
from app.models.user import User
from app.schemas.metrics import MetricsOut

router = APIRouter()

_INFERRED_SUCCESS_STATUSES = ("in_review", "approved")


def _aware(dt: datetime) -> datetime:
    """Normalize a possibly-naive DB datetime (SQLite) to tz-aware UTC (mirrors
    api/routes/profile.py's helper)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _avg(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@router.get("", response_model=MetricsOut)
def get_metrics(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MetricsOut:
    runs = db.scalars(select(PipelineRun).where(PipelineRun.user_id == user.id)).all()

    forms_total = len(runs)
    forms_by_status = Counter(r.terminal_status for r in runs)

    avg_fill = _avg([r.fill_latency_ms for r in runs if r.fill_latency_ms is not None])
    avg_review = _avg([r.review_latency_ms for r in runs if r.review_latency_ms is not None])

    total_fields = sum(r.total_fields for r in runs)
    autofilled_fields = sum(r.autofilled_fields for r in runs)
    autofill_rate = _ratio(autofilled_fields, total_fields)

    inferred_runs = [r for r in runs if r.schema_source == "inferred"]
    inferred_forms_total = len(inferred_runs)
    inferred_success = sum(
        1 for r in inferred_runs if r.terminal_status in _INFERRED_SUCCESS_STATUSES
    )
    schema_inference_success_rate = _ratio(inferred_success, inferred_forms_total)

    approved_as_is_total = sum(r.approved_as_is for r in runs if r.approved_as_is is not None)
    corrected_total = sum(r.corrected_fields for r in runs if r.corrected_fields is not None)
    accuracy_proxy = _ratio(approved_as_is_total, approved_as_is_total + corrected_total)

    review_ms_total = sum(r.review_latency_ms for r in runs if r.review_latency_ms is not None)
    measured_review_seconds = review_ms_total // 1000
    estimated_manual_seconds = total_fields * settings.manual_seconds_per_field
    estimated_time_saved_seconds = estimated_manual_seconds - measured_review_seconds

    # One grouped read over form_fields joined to forms, scoped to this user (mirrors
    # History's anti-N+1 pattern) — metadata columns only, never decrypted.
    field_rows = db.execute(
        select(
            FormField.confidence_band,
            FormField.mapping_tier,
            FormField.verified,
            FormField.value_encrypted,
        )
        .join(Form, Form.id == FormField.form_id)
        .where(Form.user_id == user.id)
    ).all()

    total_field_rows = len(field_rows)
    high_confidence_count = sum(1 for row in field_rows if row.confidence_band == "high")
    high_confidence_rate = _ratio(high_confidence_count, total_field_rows)

    mapping_tier_distribution = Counter(
        row.mapping_tier for row in field_rows if row.mapping_tier is not None
    )

    fields_with_value = [row for row in field_rows if row.value_encrypted is not None]
    verified_count = sum(1 for row in fields_with_value if row.verified)
    verification_pass_rate = _ratio(verified_count, len(fields_with_value))

    # OCR-ingestion latency (Phase 1 timestamps, already recoverable — no new column).
    doc_rows = db.execute(
        select(Document.created_at, Document.extracted_at).where(
            Document.user_id == user.id, Document.extracted_at.isnot(None)
        )
    ).all()
    ocr_latencies_ms = [
        int((_aware(extracted_at) - _aware(created_at)).total_seconds() * 1000)
        for created_at, extracted_at in doc_rows
    ]
    avg_ocr = _avg(ocr_latencies_ms)

    return MetricsOut(
        forms_total=forms_total,
        forms_by_status=dict(forms_by_status),
        avg_fill_latency_ms=avg_fill,
        avg_review_latency_ms=avg_review,
        avg_ocr_latency_ms=avg_ocr,
        total_fields=total_fields,
        autofilled_fields=autofilled_fields,
        autofill_rate=autofill_rate,
        high_confidence_rate=high_confidence_rate,
        inferred_forms_total=inferred_forms_total,
        schema_inference_success_rate=schema_inference_success_rate,
        mapping_tier_distribution=dict(mapping_tier_distribution),
        verification_pass_rate=verification_pass_rate,
        accuracy_proxy=accuracy_proxy,
        manual_seconds_per_field=settings.manual_seconds_per_field,
        estimated_manual_seconds=estimated_manual_seconds,
        measured_review_seconds=measured_review_seconds,
        estimated_time_saved_seconds=estimated_time_saved_seconds,
        forms_per_profile=forms_total,
    )
