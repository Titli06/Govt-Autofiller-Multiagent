"""PipelineRun model: one row per form fill, holding coarse latency spans + snapshot
counters so aggregate metrics reads are cheap and self-contained (PRD §9, Phase 6,
SPEC-PHASE6.md §3.2).

Written at fill completion (record_fill), updated at approval (record_review). Not an
audit trail — this is user data like everything else, and is deleted wholesale by the
Phase-5 purge (SPEC-PHASE6.md Decision 6).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # One row per form; a re-run of fill_form_task upserts (record_fill), never
    # duplicates.
    form_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("forms.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    # Denormalized for per-user aggregate reads AND the explicit purge delete — both
    # key off user_id directly, no join needed (mirrors the rest of the purge).
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schema_source: Mapped[str] = mapped_column(String(16), nullable=False)  # template | inferred
    # approved | in_review | failed | type_mismatch
    terminal_status: Mapped[str] = mapped_column(String(32), nullable=False)

    # Coarse spans only (Decision 4 — no per-stage sub-timers).
    # fill = Form.created_at -> filled_at (PRD "upload -> filled form ready").
    fill_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # review = filled_at -> approved. 0 when a form auto-approves at fill (no human
    # step); NULL while still in_review/failed/type_mismatch; set/overwritten by
    # record_review() at (re-)approval.
    review_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Snapshot counters — cheap aggregate reads; also derivable from form_fields, kept
    # here for a self-contained per-run row.
    total_fields: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    autofilled_fields: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # not needs_review
    reviewed_fields: Mapped[int | None] = mapped_column(Integer, nullable=True)  # needs_review, set at approval
    approved_as_is: Mapped[int | None] = mapped_column(Integer, nullable=True)  # approved | approved_blank
    corrected_fields: Mapped[int | None] = mapped_column(Integer, nullable=True)  # review_action == corrected

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
