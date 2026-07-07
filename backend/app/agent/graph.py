"""LangGraph state graph that orchestrates the fill pipeline.

Node order:
    form_schema -> profile_lookup -> document_verification -> confidence_scorer
Then a conditional branch out of form_schema:
    type_mismatch  -> END (no fields filled)
    known type      -> continue to profile_lookup

The graph never emits a submitted form; its output is a draft plus the review queue.
High-stakes fields (money, legal declarations, non-exact date/ID) are ALWAYS routed
to review regardless of confidence.

document_verification (Phase 3) re-checks each filled value against its source
document — a deterministic snippet re-ground first, escalating to a vision-LLM only on
a miss (SPEC-PHASE3.md §3) — before confidence_scorer prices the result. Field
placement/rendering is NOT a graph concern; it happens deterministically at download
time from the template (services/form_renderer.py).

Phase 4 (SPEC-PHASE4.md §6.2): `form_schema` grows a template-vs-inference branch.
declared type known -> the Phase 2/3 template path, unchanged (only a CONFIDENT
DIFFERENT known type is a type_mismatch). declared type unseen but classify_form
CONFIDENTLY recognizes a known type -> adopt that template (Decision 2; not a
mismatch). Otherwise -> genuinely unrecognized: infer the schema via Document AI
field detection + LLM semantic label mapping (field_mapping_tool), producing the
same TemplateField-shaped specs the template path emits, so profile_lookup ->
document_verification -> confidence_scorer consume them identically. profile_lookup
additionally stamps `inferred` (from state["schema_source"]) on every field dict —
confidence_scorer_tool needs it even for a no_mapping inferred field, which has no
mapping_cap of its own (SPEC-PHASE4.md §6.6).

Nodes are pure over (state, config): every external input (the decrypted profile
snapshot, the form's page images, the form classifier callable, the document
verifier callable, and — Phase 4 — the field detector and label mapper callables) is
injected via config["configurable"] by the caller (fill_form_task), so the graph is
testable with fakes — no DB access, no real vision-LLM/Document AI call.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from app.agent.state import AgentState
from app.agent.tools import (
    confidence_scorer_tool,
    document_verification_tool,
    field_mapping_tool,
    profile_lookup_tool,
)
from app.agent.tools.form_schema_tool import known_types, load_template, resolve_form_type


def _form_schema_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    cfg = config["configurable"]
    declared = state["declared_form_type"]
    known = set(known_types())
    detected = cfg["classifier"](cfg["images"], known_types())

    if declared in known:
        # Phase 2/3 behavior, unchanged: only a CONFIDENT DIFFERENT known type blocks.
        resolved_type, mismatch = resolve_form_type(declared, detected)
        if mismatch:
            return {
                "form_type": resolved_type,
                "detected_form_type": detected,
                "type_mismatch": True,
                "schema_source": "template",
                "field_specs": [],
            }
        template = load_template(resolved_type)
        return {
            "form_type": resolved_type,
            "detected_form_type": detected,
            "type_mismatch": False,
            "schema_source": "template",
            "field_specs": template.required_fields,
        }

    if detected in known:
        # Decision 2: a confident detection of a KNOWN type overrides an unseen
        # declared label — adopt the template (better placement + hand-authored
        # high_stakes) rather than inferring a form we already have. This is NOT a
        # type_mismatch: the user didn't declare a *conflicting known* type.
        template = load_template(detected)
        return {
            "form_type": detected,
            "detected_form_type": detected,
            "type_mismatch": False,
            "schema_source": "template",
            "field_specs": template.required_fields,
        }

    # Genuinely unrecognized (Decision 1): infer the schema via Document AI field
    # detection + LLM semantic label mapping.
    detected_fields = cfg["field_detector"](cfg["images"])
    field_specs = field_mapping_tool.infer_schema(detected_fields, cfg["label_mapper"])
    return {
        "form_type": declared,
        "detected_form_type": detected,
        "type_mismatch": False,
        "schema_source": "inferred",
        "field_specs": field_specs,
    }


def _route_after_schema(state: AgentState) -> str:
    return END if state["type_mismatch"] else "profile_lookup"


def _profile_lookup_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    snapshot = config["configurable"]["snapshot"]
    inferred = state["schema_source"] == "inferred"
    fields = profile_lookup_tool.lookup(state["field_specs"], snapshot)
    for f in fields:
        f["inferred"] = inferred
    return {"fields": fields}


def _document_verification_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    verifier = config["configurable"]["verifier"]
    return {"fields": document_verification_tool.verify(state["fields"], verifier)}


def _confidence_scorer_node(state: AgentState) -> dict[str, Any]:
    return {"fields": confidence_scorer_tool.score(state["fields"])}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("form_schema", _form_schema_node)
    graph.add_node("profile_lookup", _profile_lookup_node)
    graph.add_node("document_verification", _document_verification_node)
    graph.add_node("confidence_scorer", _confidence_scorer_node)

    graph.set_entry_point("form_schema")
    graph.add_conditional_edges("form_schema", _route_after_schema)
    graph.add_edge("profile_lookup", "document_verification")
    graph.add_edge("document_verification", "confidence_scorer")
    graph.add_edge("confidence_scorer", END)

    return graph.compile()
