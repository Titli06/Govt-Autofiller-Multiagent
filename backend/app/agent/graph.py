"""LangGraph state graph that orchestrates the fill pipeline.

Node order:
    form_schema -> profile_lookup -> document_verification -> confidence_scorer
Then a conditional branch per field:
    high confidence            -> auto-fill
    low conf. or high-stakes   -> route to HITL review

The graph never emits a submitted form; its output is a draft plus the review queue.
High-stakes fields (money, legal declarations, non-exact date/ID) are ALWAYS routed
to review regardless of confidence.
"""

# TODO: build_graph() -> compiled LangGraph app
