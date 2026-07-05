"""Profile field request/response contracts.

display_value is always PII-safe: masked for high-sensitivity fields (Aadhaar/PAN),
decrypted plaintext otherwise — a full Aadhaar/PAN number must never appear in a response.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class ProfileFieldSource(BaseModel):
    document_id: uuid.UUID
    doc_type: str


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
