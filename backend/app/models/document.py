"""Document model: metadata for an uploaded ID doc; raw bytes live in S3/MinIO.

ocr_status lifecycle: pending -> processing -> (extracted | partial | failed | type_mismatch).
See SPEC-PHASE1.md §4.1, §6.5.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # User's declared type at upload time: "aadhaar" | "pan".
    declared_doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Vision-LLM's own classification; compared against declared_doc_type (Decision 6).
    detected_doc_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # Safe, non-PII reason on failure/mismatch — never raw model output or PII (CLAUDE.md).
    ocr_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
