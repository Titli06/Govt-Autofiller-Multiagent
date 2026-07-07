"""Profile field request/response contracts.

display_value is always PII-safe: masked for high-sensitivity fields (Aadhaar/PAN),
decrypted plaintext otherwise — a full Aadhaar/PAN number must never appear in a response.

document_id/doc_type are nullable (Phase 3): a manual candidate synthesized from a
form-review correction (SPEC-PHASE3.md Decision 11) has no source document.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class ProfileFieldSource(BaseModel):
    document_id: uuid.UUID | None
    doc_type: str | None


class ProfileFieldOut(BaseModel):
    id: uuid.UUID
    field_name: str
    display_value: str
    confidence: float
    confidence_band: str
    high_stakes: bool
    status: str
    source: ProfileFieldSource


class ProfileOut(BaseModel):
    fields: list[ProfileFieldOut]


class CorrectFieldRequest(BaseModel):
    value: str
