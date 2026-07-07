"""Form / FormField request/response contracts.

display_value mirrors Phase 1's ProfileFieldOut convention: masked for high-sensitivity
profile_key (Aadhaar/PAN), decrypted plaintext otherwise, null when the field is unfilled
(no_mapping / no_candidate) — a full Aadhaar/PAN must never appear in a response.

Phase 3 adds the review projection/action contracts (SPEC-PHASE3.md §8.2/§8.3). The
same masking rule applies there — only the downloaded PDF ever carries a full
Aadhaar/PAN (§8.6).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

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
    schema_source: str
    fill_error: str | None
    page_count: int | None
    created_at: datetime
    filled_at: datetime | None
    fields: list[FormFieldOut]


# --- Phase 3: verification + HITL review + download -----------------------------------


class FormFieldReviewOut(BaseModel):
    id: uuid.UUID
    field_name: str
    profile_key: str | None
    display_value: str | None
    confidence: float
    confidence_band: str
    verified: bool
    verification_method: str | None
    high_stakes: bool
    transformed: bool
    needs_review: bool
    review_reason: str | None
    reviewed: bool
    review_action: str | None
    outstanding: bool  # needs_review AND NOT reviewed — this is what blocks download
    source: FormFieldSource


class FormReviewOut(BaseModel):
    id: uuid.UUID
    form_type: str
    display_name: str
    status: str
    schema_source: str
    download_ready: bool
    total_fields: int
    outstanding_fields: int
    placement_warning: str | None
    fields: list[FormFieldReviewOut]


class ReviewActionRequest(BaseModel):
    field_id: uuid.UUID
    action: Literal["approve", "correct", "approve_blank"]
    value: str | None = None
    propagate_to_profile: bool = False


class ReviewActionResponse(BaseModel):
    field: FormFieldReviewOut
    status: str
    download_ready: bool
    warning: str | None = None
