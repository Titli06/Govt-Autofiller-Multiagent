"""Shared state object threaded through the LangGraph pipeline.

Carries the form being filled, per-field candidate values, verification results,
confidence scores, and the running list of fields routed to human review.
"""

from __future__ import annotations

from typing import Any, TypedDict


class FieldResult(TypedDict):
    field_name: str  # the form's own field name
    profile_key: str | None  # canonical profile vocabulary key; None => no_mapping
    value: str | None  # filled (possibly reformatted) value; None when unfillable
    source_doc_id: str | None  # provenance for auditability
    profile_field_id: str | None  # which profile candidate this was filled from
    high_stakes: bool  # from the template (FR8 category)
    transformed: bool  # a format transform changed the value (recorded, not auto-flagged)
    verified: bool  # exact match against source document — ALWAYS False in Phase 2 (Phase 3 sets it)
    confidence: float  # provisional score, inherited from the profile candidate (§6.4)
    confidence_band: str  # high | medium | low
    needs_review: bool  # computed now, enforced in Phase 3
    review_reason: str | None  # single, precedence-ordered reason (see confidence_scorer_tool)
    flags: dict[str, Any]  # full audit of every trigger considered


class AgentState(TypedDict):
    user_id: str
    form_id: str
    declared_form_type: str
    detected_form_type: str | None
    type_mismatch: bool
    form_type: str | None  # resolved type (== declared unless mismatch)
    field_specs: list[Any]  # template required_fields (TemplateField), set by form_schema node
    # Intermediate lookup dicts before confidence_scorer runs, FieldResult-shaped after —
    # loosely typed because the pipeline has two shapes in flight (see profile_lookup_tool
    # / confidence_scorer_tool).
    fields: list[dict[str, Any]]
