"""Document request/response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentUploadResponse(BaseModel):
    document_id: uuid.UUID
    ocr_status: str


class DocumentStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    declared_doc_type: str
    detected_doc_type: str | None
    ocr_status: str
    ocr_error: str | None
    page_count: int | None
    created_at: datetime
    extracted_at: datetime | None
