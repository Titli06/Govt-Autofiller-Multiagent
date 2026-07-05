"""Shared state object threaded through the LangGraph pipeline.

Carries the form being filled, per-field candidate values, verification results,
confidence scores, and the running list of fields routed to human review.
"""

from typing import TypedDict


class FieldResult(TypedDict):
    field_name: str
    value: str | None
    source_doc_id: str | None  # provenance for auditability
    verified: bool             # exact match against source document
    confidence: float
    needs_review: bool
    review_reason: str | None  # e.g. "low_confidence", "monetary", "legal_declaration"


class AgentState(TypedDict):
    user_id: str
    form_id: str
    form_type: str | None
    required_fields: list[str]
    fields: list[FieldResult]
