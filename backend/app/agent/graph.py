"""LangGraph state graph that orchestrates the fill pipeline.

Node order:
    form_schema -> profile_lookup -> confidence_scorer
Then a conditional branch out of form_schema:
    type_mismatch  -> END (no fields filled)
    known type      -> continue to profile_lookup

The graph never emits a submitted form; its output is a draft plus the review queue.
High-stakes fields (money, legal declarations, non-exact date/ID) are ALWAYS routed
to review regardless of confidence.

Phase 3's document_verification_tool is inserted BETWEEN profile_lookup and
confidence_scorer — that seam is left obvious, not built yet.

Nodes are pure over (state, config): every external input (the decrypted profile
snapshot, the form's page images, and the form classifier callable) is injected via
config["configurable"] by the caller (fill_form_task), so the graph is testable with
fakes — no DB access, no real vision-LLM call.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from app.agent.state import AgentState
from app.agent.tools import confidence_scorer_tool, profile_lookup_tool
from app.agent.tools.form_schema_tool import known_types, load_template, resolve_form_type


def _form_schema_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    cfg = config["configurable"]
    template = load_template(state["declared_form_type"])
    detected = cfg["classifier"](cfg["images"], known_types())
    resolved_type, mismatch = resolve_form_type(state["declared_form_type"], detected)
    return {
        "form_type": resolved_type,
        "detected_form_type": detected,
        "type_mismatch": mismatch,
        "field_specs": [] if mismatch else template.required_fields,
    }


def _route_after_schema(state: AgentState) -> str:
    return END if state["type_mismatch"] else "profile_lookup"


def _profile_lookup_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    snapshot = config["configurable"]["snapshot"]
    return {"fields": profile_lookup_tool.lookup(state["field_specs"], snapshot)}


def _confidence_scorer_node(state: AgentState) -> dict[str, Any]:
    return {"fields": confidence_scorer_tool.score(state["fields"])}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("form_schema", _form_schema_node)
    graph.add_node("profile_lookup", _profile_lookup_node)
    graph.add_node("confidence_scorer", _confidence_scorer_node)

    graph.set_entry_point("form_schema")
    graph.add_conditional_edges("form_schema", _route_after_schema)
    graph.add_edge("profile_lookup", "confidence_scorer")
    graph.add_edge("confidence_scorer", END)

    return graph.compile()
