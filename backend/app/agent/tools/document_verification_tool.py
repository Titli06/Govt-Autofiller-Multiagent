"""document_verification_tool — cross-check each candidate value against its source doc.

This is the trust layer that prevents silent drift between profile data and the
finalized form. An exact match to the original document is the strongest signal the
confidence scorer has; never treat an unverified value as high-confidence.

Hybrid strategy (SPEC-PHASE3.md Decision 1, §3): first re-ground the *formatted* form
value deterministically against the selected candidate's source snippet
(`snippet_contains` + typed semantic equality for dates/IDs) — free, unit-testable,
and grounded in CLAUDE.md's "confidence from source-document match, not LLM
self-report." Only on a deterministic miss does it escalate to a vision-LLM check
against the source document image. A transient LLM error propagates (the caller
retries the job) — it never silently resolves to "verified."

Pure over its inputs plus an injected `verifier` callable — no DB/crypto access, so
it's testable with a stub (see agent/graph.py's document_verification node).
"""

from __future__ import annotations

import re
from typing import Any, Callable

from app.core.validators import normalize_aadhaar, normalize_pan, parse_dob, snippet_contains

# (value, source_doc_id) -> matches. Injected by the worker; calls the vision-LLM on
# escalation only (SPEC-PHASE3.md default implementation choices).
Verifier = Callable[[str, str | None], bool]

_ID_NORMALIZERS = {"aadhaar_number": normalize_aadhaar, "pan_number": normalize_pan}

# Loose date-token finder used to locate a date *within* a longer snippet (e.g.
# "DOB: 12/04/1998") — parse_dob itself expects the whole string to match one format.
_DATE_TOKEN_RE = re.compile(
    r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
)


def _find_date_in_snippet(snippet: str):
    for match in _DATE_TOKEN_RE.finditer(snippet):
        parsed = parse_dob(match.group())
        if parsed is not None:
            return parsed
    return None


def deterministic_match(profile_key: str | None, value: str, snippet: str | None) -> str:
    """Returns "exact" | "semantic" | "miss" (SPEC-PHASE3.md §3.2 algorithm table).

    An empty/None snippet always misses — that forces LLM escalation rather than a
    false pass on missing grounding data.
    """
    if not snippet:
        return "miss"

    if snippet_contains(value, snippet):
        return "exact"

    if profile_key == "dob":
        value_date = parse_dob(value)
        snippet_date = _find_date_in_snippet(snippet)
        if value_date is not None and snippet_date is not None and value_date == snippet_date:
            return "semantic"
        return "miss"

    normalizer = _ID_NORMALIZERS.get(profile_key or "")
    if normalizer is not None:
        normalized_value = normalizer(value)
        normalized_snippet = normalizer(snippet)
        if normalized_value and normalized_value in normalized_snippet:
            return "semantic"
        return "miss"

    # Free text (name/address/gender/...): exact-or-nothing, no semantic tier.
    return "miss"


def verify(fields: list[dict[str, Any]], verifier: Verifier) -> list[dict[str, Any]]:
    """Annotates each field dict with `verified: bool` and `verification_method:
    str | None`. Missing fields (no_mapping/no_candidate) are left unverified — there
    is nothing to check; the missing flag is the scorer's concern."""
    results: list[dict[str, Any]] = []
    for item in fields:
        if item["missing"] is not None:
            results.append({**item, "verified": False, "verification_method": None})
            continue

        value = item["value"]
        snippet = item.get("candidate_snippet")
        det = deterministic_match(item["profile_key"], value, snippet)

        if det == "exact":
            verified, method = True, "exact"
        elif det == "semantic":
            verified, method = True, "semantic"
        else:
            matched = verifier(value, item["source_doc_id"])
            verified, method = matched, "llm"

        results.append({**item, "verified": verified, "verification_method": method})
    return results
