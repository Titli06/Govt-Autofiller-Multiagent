"""Profile model: the verified personal data store.

Field values are stored field-level encrypted (see core/encryption.py). Each ProfileField
row is one *candidate* — keyed by (profile, field_name, source_doc) — not a single
canonical value, so the same logical field extracted from two different documents keeps
both candidates with their own confidence and provenance (SPEC-PHASE1.md Decision 1).
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
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    fields: Mapped[list["ProfileField"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class ProfileField(Base):
    __tablename__ = "profile_fields"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "field_name", "source_doc_id", name="uq_profile_field_candidate"
        ),
        Index("ix_profile_fields_profile_field_name", "profile_id", "field_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Provenance: which uploaded document this candidate value came from. NULL for a
    # manual candidate synthesized from a hand-typed form-review correction (Phase 3
    # Decision 11) — there is no source document to point at.
    source_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # "document" (OCR-extracted, Phase 1) | "manual" (hand-typed in Phase 3 review)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="document")

    # Immutable audit of what OCR produced (AES-256-GCM ciphertext, see core/encryption.py).
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Set only if the user edits the value; effective value = corrected if present else extracted.
    corrected_value_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Display-safe masked form; required for high-sensitivity fields (Aadhaar/PAN), else null.
    value_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Verbatim snippet the value was read from (encrypted — it contains the value).
    source_snippet_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_band: Mapped[str] = mapped_column(String(16), nullable=False)  # high|medium|low
    high_stakes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # confirmed | needs_confirmation | user_confirmed | user_corrected | failed_validation
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Audit of why the score landed where it did: {snippet_contains, format_valid, normalized}.
    validators: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    profile: Mapped["Profile"] = relationship(back_populates="fields")

    @property
    def effective_value_encrypted(self) -> bytes:
        """The value_encrypted, or the corrected_value_encrypted if the user edited it."""
        return self.corrected_value_encrypted or self.value_encrypted
