"""confidence_scorer_tool — assign a per-field confidence score and review flag.

Scoring policy (source-grounded, NOT LLM self-report):
    exact match to source document   -> high
    inferred / derived value          -> lower
    missing value                     -> flagged

A field is routed to mandatory human review when its score is below
CONFIDENCE_THRESHOLD, OR it is high-stakes (money, legal declaration, non-exact
date/ID match) regardless of score.
"""

# TODO: score(fields) -> set confidence + needs_review + review_reason
