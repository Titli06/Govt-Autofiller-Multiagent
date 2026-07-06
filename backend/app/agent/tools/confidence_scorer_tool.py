"""confidence_scorer_tool — assign a per-field provisional confidence score and review flag.

Scoring policy (SPEC-PHASE2.md Decisions 5/9/10/11) — provisional, NOT a fresh
verification (that's Phase 3's document_verification_tool):
    missing value                         -> confidence 0.0
    user-confirmed/corrected candidate    -> confidence 1.0 (fully trusted)
    otherwise                             -> inherit the candidate's grounded confidence

A field is flagged needs_review (computed now, enforced in Phase 3) when it is missing,
high-stakes (money/legal/date/ID, FR8), its source candidate is itself unresolved
(trust doesn't launder through the fill step), or its confidence is below
CONFIDENCE_THRESHOLD. A format transform is recorded but never itself a review trigger.
"""

from __future__ import annotations

from typing import Any

from app.config import settings

_USER_ACTED_STATUSES = {"user_confirmed", "user_corrected"}
_UNVERIFIED_STATUSES = {"needs_confirmation", "failed_validation"}


def _band(confidence: float) -> str:
    if confidence >= settings.ocr_confidence_high:
        return "high"
    if confidence >= settings.ocr_confidence_medium:
        return "medium"
    return "low"


def _confidence(item: dict[str, Any]) -> float:
    if item["missing"] is not None:
        return 0.0
    if item["candidate_status"] in _USER_ACTED_STATUSES:
        return 1.0
    return item["candidate_confidence"]


def _review_reason(flags: dict[str, Any]) -> str | None:
    # Precedence order (SPEC-PHASE2.md §6.5): missing > high_stakes > unverified_source
    # > low_confidence. transformed is never a reason.
    if flags["missing"] is not None:
        return flags["missing"]
    if flags["high_stakes"]:
        return "high_stakes"
    if flags["unverified_source"]:
        return "unverified_source"
    if flags["low_confidence"]:
        return "low_confidence"
    return None


def score(lookups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for item in lookups:
        confidence = _confidence(item)
        flags = {
            "missing": item["missing"],
            "high_stakes": item["high_stakes"],
            "unverified_source": item["candidate_status"] in _UNVERIFIED_STATUSES,
            "low_confidence": confidence < settings.confidence_threshold,
            "transformed": item["transformed"],
        }
        review_reason = _review_reason(flags)
        results.append(
            {
                "field_name": item["field_name"],
                "profile_key": item["profile_key"],
                "value": item["value"],
                "profile_field_id": item["profile_field_id"],
                "source_doc_id": item["source_doc_id"],
                "high_stakes": item["high_stakes"],
                "transformed": item["transformed"],
                "verified": False,  # Phase 3's document_verification_tool sets this
                "confidence": confidence,
                "confidence_band": _band(confidence),
                "needs_review": review_reason is not None,
                "review_reason": review_reason,
                "flags": flags,
            }
        )
    return results
