"""profile_lookup_tool — map form fields to stored profile values.

Deterministic mapping via each template field's profile_key (SPEC-PHASE2.md Decision 2):
known templates already declare which canonical profile field backs each form field, so
this tool just selects the best candidate and reformats it. Semantic (LLM) matching for
unlabeled/inferred forms is Phase 4.

Pure over an already-decrypted ProfileSnapshot — no DB access, no crypto — so it's
testable with fakes. fill_form_task builds the snapshot and hands it in via LangGraph's
invocation config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.agent.tools.form_schema_tool import TemplateField

# Candidates whose value the user has personally acted on outrank any grounded score
# (SPEC-PHASE2.md Decision 3).
_USER_ACTED_STATUSES = {"user_confirmed", "user_corrected"}


@dataclass
class CandidateView:
    """One profile candidate for a canonical field, decrypted and ready to fill with."""

    profile_field_id: str
    source_doc_id: str | None  # None for a manual candidate (Phase 3 Decision 11)
    doc_type: str
    value: str
    confidence: float
    status: str
    created_at: datetime
    # Decrypted verbatim source snippet, carried forward so document_verification_tool
    # can re-ground the formatted value without touching the DB/crypto (Phase 3 §3.1).
    # None for a candidate with no stored snippet (e.g. a manual write-back).
    source_snippet: str | None = None


# profile_key -> every candidate the profile has for it (built by fill_form_task).
ProfileSnapshot = dict[str, list[CandidateView]]


def _select_candidate(candidates: list[CandidateView]) -> CandidateView:
    """User-acted first, then highest confidence, then most recent (Decision 3)."""
    return max(candidates, key=lambda c: (c.status in _USER_ACTED_STATUSES, c.confidence, c.created_at))


def apply_format(value: str, fmt: str) -> tuple[str, bool]:
    """Returns (formatted_value, transformed). A date that fails to parse is left
    verbatim (transformed=False) — an unparsable value surfaces via confidence
    scoring/review rather than silently vanishing (SPEC-PHASE2.md §3.3)."""
    if fmt == "upper":
        result = value.upper()
    elif fmt == "single_line":
        result = re.sub(r"\s+", " ", value).strip()
    elif fmt.startswith("date:"):
        result = _reformat_date(value, fmt.removeprefix("date:")) or value
    else:  # "as_is" or any other literal — verbatim
        result = value
    return result, result != value


def _reformat_date(value: str, strftime_fmt: str) -> str | None:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.strftime(strftime_fmt)


def _missing(spec: TemplateField, reason: str) -> dict[str, Any]:
    return {
        "field_name": spec.name,
        "profile_key": spec.profile_key,
        "value": None,
        "profile_field_id": None,
        "source_doc_id": None,
        "high_stakes": spec.high_stakes,
        "transformed": False,
        "candidate_confidence": None,
        "candidate_status": None,
        "candidate_snippet": None,
        "missing": reason,
    }


def lookup(field_specs: list[TemplateField], snapshot: ProfileSnapshot) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for spec in field_specs:
        if spec.profile_key is None:
            results.append(_missing(spec, "no_mapping"))
            continue

        candidates = snapshot.get(spec.profile_key) or []
        if not candidates:
            results.append(_missing(spec, "no_candidate"))
            continue

        chosen = _select_candidate(candidates)
        value, transformed = apply_format(chosen.value, spec.format)
        results.append(
            {
                "field_name": spec.name,
                "profile_key": spec.profile_key,
                "value": value,
                "profile_field_id": chosen.profile_field_id,
                "source_doc_id": chosen.source_doc_id,
                "high_stakes": spec.high_stakes,
                "transformed": transformed,
                "candidate_confidence": chosen.confidence,
                "candidate_status": chosen.status,
                "candidate_snippet": chosen.source_snippet,
                "missing": None,
            }
        )
    return results
