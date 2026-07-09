"""Pipeline metrics (Phase 6, PRD §9) — these are part of the deliverable, not
optional telemetry.

Two entry points, both called from within an already-open transaction (the caller's
terminal `db.commit()` persists the metrics row atomically with the status change
they're derived from — no separate commit here):

    record_fill(db, form, field_results)   -- fill_form_task's terminal branches
    record_review(db, form)                -- the review endpoint, on reaching "approved"

Coarse spans only (SPEC-PHASE6.md Decision 4) — no per-stage sub-timers. Idempotent:
record_fill upserts by the unique form_id (a re-run of fill_form_task overwrites, never
duplicates); record_review overwrites on re-approval after a Phase-3 reopen, so the row
always reflects the *final* state.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.form import Form, FormField
from app.models.metrics import PipelineRun

_APPROVED_ACTIONS = ("approved", "approved_blank")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """Normalize a possibly-naive DB datetime (SQLite) to tz-aware UTC (mirrors
    api/routes/profile.py's helper)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _span_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return int((_aware(end) - _aware(start)).total_seconds() * 1000)


def record_fill(db: Session, form: Form, field_results: list[dict] | None) -> None:
    """Upsert the pipeline_run row when a fill reaches a terminal state. field_results
    is None on a failed/type_mismatch fill (zero counts)."""
    run = db.scalar(select(PipelineRun).where(PipelineRun.form_id == form.id))
    if run is None:
        run = PipelineRun(form_id=form.id)
        db.add(run)

    run.user_id = form.user_id
    run.schema_source = form.schema_source
    run.terminal_status = form.status
    run.fill_latency_ms = _span_ms(form.created_at, form.filled_at)

    fields = field_results or []
    run.total_fields = len(fields)
    run.autofilled_fields = sum(1 for f in fields if not f["needs_review"])

    if form.status == "approved":
        # Zero outstanding fields -> auto-approved at fill time, no human review span.
        run.review_latency_ms = 0
        run.reviewed_fields = 0
        run.approved_as_is = 0
        run.corrected_fields = 0
    else:
        # in_review/failed/type_mismatch: review span not yet known.
        run.review_latency_ms = None
        run.reviewed_fields = None
        run.approved_as_is = None
        run.corrected_fields = None


def record_review(db: Session, form: Form) -> None:
    """Update the row when a form reaches `approved` via the review endpoint,
    including a re-approval after a Phase-3 reopen (overwrite, not append). A safe
    no-op if no row exists (a pre-Phase-6 form)."""
    run = db.scalar(select(PipelineRun).where(PipelineRun.form_id == form.id))
    if run is None:
        return

    rows = db.query(FormField).filter(FormField.form_id == form.id).all()
    run.terminal_status = form.status
    run.review_latency_ms = _span_ms(form.filled_at, _now())
    run.reviewed_fields = sum(1 for r in rows if r.needs_review)
    run.approved_as_is = sum(1 for r in rows if r.review_action in _APPROVED_ACTIONS)
    run.corrected_fields = sum(1 for r in rows if r.review_action == "corrected")
