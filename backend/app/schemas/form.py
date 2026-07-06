"""Form / FormField request/response contracts.

display_value mirrors Phase 1's ProfileFieldOut convention: masked for high-sensitivity
profile_key (Aadhaar/PAN), decrypted plaintext otherwise, null when the field is unfilled
(no_mapping / no_candidate) — a full Aadhaar/PAN must never appear in a response.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class FormUploadResponse(BaseModel):
    form_id: uuid.UUID
    status: str


class FormFieldSource(BaseModel):
    profile_field_id: uuid.UUID | None
    document_id: uuid.UUID | None
    doc_type: str | None


class FormFieldOut(BaseModel):
    id: uuid.UUID
    field_name: str
    profile_key: str | None
    display_value: str | None
    confidence: float
    confidence_band: str
    high_stakes: bool
    transformed: bool
    needs_review: bool
    review_reason: str | None
    reviewed: bool
    source: FormFieldSource


class FormOut(BaseModel):
    id: uuid.UUID
    form_type: str
    display_name: str
    detected_form_type: str | None
    status: str
    fill_error: str | None
    page_count: int | None
    created_at: datetime
    filled_at: datetime | None
    fields: list[FormFieldOut]
