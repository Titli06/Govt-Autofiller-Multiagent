"""History request/response contracts (Phase 5, FR11).

Read-only, metadata-only projection of a user's past forms — no field values are
read or returned here (see api/routes/forms.py for per-field detail via the
review/get endpoints, which the frontend deep-links to).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class HistoryItemOut(BaseModel):
    id: uuid.UUID
    form_type: str  # declared_form_type — free-text for an inferred form (Phase 4)
    display_name: str
    schema_source: str  # "template" | "inferred"
    status: str  # in_review | approved | failed | type_mismatch (pending/processing excluded)
    fill_error: str | None
    total_fields: int
    outstanding_fields: int  # needs_review AND NOT reviewed
    download_ready: bool  # status == "approved"
    created_at: datetime
    filled_at: datetime | None


class HistoryOut(BaseModel):
    forms: list[HistoryItemOut]
