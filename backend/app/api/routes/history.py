"""History routes: list past filled forms for reuse reference (FR11, SPEC-PHASE5.md §6.1).

Phase 6 (SPEC-PHASE6.md §6.6): each row also carries its per-form latency
(fill_latency_ms/review_latency_ms), read from pipeline_run via one grouped query
alongside the existing field-count read — no N+1, no new endpoint. A pre-Phase-6 form
(no pipeline_run row) surfaces `null` for both.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.routes.forms import _display_name
from app.models.form import Form, FormField
from app.models.metrics import PipelineRun
from app.models.user import User
from app.schemas.history import HistoryItemOut, HistoryOut

router = APIRouter()

# Non-transient statuses only — pending/processing forms are still being worked on
# (Decision 5). failed/type_mismatch stay visible with their safe reason rather than
# silently disappearing.
_VISIBLE_STATUSES = ("in_review", "approved", "failed", "type_mismatch")


@router.get("", response_model=HistoryOut)
def list_history(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> HistoryOut:
    forms = db.scalars(
        select(Form)
        .where(Form.user_id == user.id, Form.status.in_(_VISIBLE_STATUSES))
        .order_by(Form.created_at.desc())
    ).all()

    # One grouped read for all field counts (not an N+1 per form), aggregated in
    # Python so it stays portable across the SQLite (tests) / Postgres (prod) split.
    counts: dict = defaultdict(lambda: {"total": 0, "outstanding": 0})
    # One grouped read for per-form latency (Phase 6) — a form without a pipeline_run
    # row (pre-Phase-6) simply isn't in this map, and surfaces null for both spans.
    latencies: dict = {}
    if forms:
        form_ids = [f.id for f in forms]
        rows = db.execute(
            select(FormField.form_id, FormField.needs_review, FormField.reviewed).where(
                FormField.form_id.in_(form_ids)
            )
        ).all()
        for form_id, needs_review, reviewed in rows:
            c = counts[form_id]
            c["total"] += 1
            if needs_review and not reviewed:
                c["outstanding"] += 1

        runs = db.execute(
            select(PipelineRun.form_id, PipelineRun.fill_latency_ms, PipelineRun.review_latency_ms).where(
                PipelineRun.form_id.in_(form_ids)
            )
        ).all()
        for form_id, fill_ms, review_ms in runs:
            latencies[form_id] = (fill_ms, review_ms)

    items = [
        HistoryItemOut(
            id=f.id,
            form_type=f.declared_form_type,
            display_name=_display_name(f),
            schema_source=f.schema_source,
            status=f.status,
            fill_error=f.fill_error,
            total_fields=counts[f.id]["total"],
            outstanding_fields=counts[f.id]["outstanding"],
            download_ready=f.status == "approved",
            created_at=f.created_at,
            filled_at=f.filled_at,
            fill_latency_ms=latencies.get(f.id, (None, None))[0],
            review_latency_ms=latencies.get(f.id, (None, None))[1],
        )
        for f in forms
    ]
    return HistoryOut(forms=items)
