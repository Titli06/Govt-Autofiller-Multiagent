"""Form model: an uploaded form + its filled fields, confidence, and review state.

Persists per-field provenance and confidence so every auto-filled value is auditable.
status lifecycle: pending -> processing -> (in_review | approved | failed |
type_mismatch). `filled` is retired as of Phase 3 — a zero-flag pipeline lands
straight on `approved`; any flagged field lands on `in_review`. See SPEC-PHASE3.md §4.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Form(Base):
    __tablename__ = "forms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # User's declared type at upload time: a known-template registry key.
    declared_form_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Vision-LLM's own classification; compared against declared_form_type (Decision 1).
    detected_form_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # Safe, non-PII reason on failure/mismatch — never raw model output or PII (CLAUDE.md).
    fill_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Cached overlay-PDF object key; NULL until first download or after a review edit
    # invalidates it (SPEC-PHASE3.md Decision 7/9).
    rendered_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Dominant page rotation (degrees) estimated at fill time; NULL if not measured.
    skew_angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Safe, non-PII advisory when the scan is significantly skewed (coordinate
    # placement may be off); NULL when upright or on the AcroForm path (Decision 15).
    placement_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class FormField(Base):
    __tablename__ = "form_fields"
    __table_args__ = (
        UniqueConstraint("form_id", "field_name", name="uq_form_field_name"),
        Index("ix_form_fields_form_id", "form_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    form_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("forms.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)  # the form's own field name
    profile_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # null => no_mapping

    # AES-256-GCM ciphertext of the filled (possibly reformatted) value; null when unfillable.
    value_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Display-safe masked form; set only when profile_key is high-sensitivity (Aadhaar/PAN).
    value_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Provenance — nullable + SET NULL so a Phase-5 profile/document purge can't delete an
    # already-generated draft; only the pointer is nulled, the encrypted snapshot stays.
    profile_field_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profile_fields.id", ondelete="SET NULL"), nullable=True
    )
    source_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    confidence: Mapped[float] = mapped_column(Float, nullable=False)  # provisional (§6.4); 0 if missing
    confidence_band: Mapped[str] = mapped_column(String(16), nullable=False)  # high|medium|low
    high_stakes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transformed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set by document_verification_tool (Phase 3); false for missing/unverified fields.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # exact | semantic | llm | user | null (missing field — nothing to verify)
    verification_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # AES-256-GCM of the user's corrected value (AAD (form_id, field_name)); effective
    # value = corrected if present else the auto-filled value_encrypted.
    corrected_value_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Computed now, enforced in Phase 3 (review UI + download gating).
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Phase 3 sets this
    # approved | corrected | approved_blank | null (unreviewed)
    review_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Full audit of every trigger considered: {missing, verification_failed, high_stakes,
    # unverified_source, low_confidence, transformed}. review_reason is the top-precedence one.
    flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def effective_value_encrypted(self) -> bytes | None:
        """The auto-filled value_encrypted, or the corrected_value_encrypted if the
        user edited it during review (mirrors ProfileField's convention)."""
        return self.corrected_value_encrypted or self.value_encrypted
