"""Shared state object threaded through the LangGraph pipeline.

Carries the form being filled, per-field candidate values, verification results,
confidence scores, and the running list of fields routed to human review.

Phase 3 note: the intermediate lookup dict (profile_lookup_tool's output, before
confidence_scorer runs) also carries `candidate_snippet: str | None` — the selected
candidate's decrypted source snippet, used by document_verification_tool to re-ground
the formatted value deterministically before any LLM escalation. It is not part of
FieldResult because it doesn't survive past verification (SPEC-PHASE3.md §3.1/§6.1).

Phase 4 note (SPEC-PHASE4.md §6.1): the intermediate lookup dict and FieldResult both
gain `mapping_tier: str | None` and `placement: dict | None` — carried straight
through from an inferred TemplateField (form_schema_tool.TemplateField.mapping_tier/
mapping_cap/placement; None for every template field). The lookup dict also gains
`inferred: bool`, stamped by the profile_lookup node from `state["schema_source"]` on
EVERY field (mapped or not) — a no_mapping inferred field has no mapping_cap but must
still be flagged `inferred_mapping` by confidence_scorer_tool. FieldResult's `flags`
dict gains an `"inferred_mapping"` key (true for every field on an inferred form).
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
    verification_method: str | None  # exact | semantic | llm | user | None (nothing to verify)
    mapping_tier: str | None  # Phase 4: exact | strong | weak | None (template field / no mapping)
    placement: dict | None  # Phase 4: inferred normalized bbox; None for template fields
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
    schema_source: str  # "template" | "inferred" (Phase 4), set by the form_schema node
    field_specs: list[Any]  # required_fields (TemplateField, template or synthesized-inferred)
    # Intermediate lookup dicts before confidence_scorer runs, FieldResult-shaped after —
    # loosely typed because the pipeline has two shapes in flight (see profile_lookup_tool
    # / confidence_scorer_tool).
    fields: list[dict[str, Any]]
