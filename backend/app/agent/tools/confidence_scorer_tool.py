"""confidence_scorer_tool — assign a per-field final confidence score and review flag.

Scoring policy (SPEC-PHASE3.md §6.3), folding in document_verification_tool's result —
this is now the tool's primary signal, not the profile candidate's self-reported trust:
    missing value                          -> confidence 0.0
    verification_failed (present, !verified) -> settings.verify_low_confidence (low band)
    verification_method == "user"           -> 1.0 (a human correction, fully trusted)
    verified exact                          -> max(inherited, ocr_confidence_high) (promoted)
    verified semantic/llm                   -> inherited, unchanged (no promotion)
where "inherited" is the Phase-2 rule: a user-confirmed/corrected candidate is 1.0,
otherwise the candidate's grounded confidence.

A field is flagged needs_review when it is missing, failed verification (new
top-precedence reason — a mismatch against the source document overrides even a
high-stakes-verified value's trust), high-stakes (money/legal/date/ID, FR8), its
source candidate is itself unresolved (trust doesn't launder through the fill step),
or its confidence is below CONFIDENCE_THRESHOLD. A format transform is recorded but
never itself a review trigger. High-stakes fields are ALWAYS flagged regardless of
verification (FR8 is unconditional) — verification only changes whether the reviewer
sees a green "verified against source" signal or a red one.
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


def _inherited_confidence(item: dict[str, Any]) -> float:
    if item["candidate_status"] in _USER_ACTED_STATUSES:
        return 1.0
    return item["candidate_confidence"]


def _confidence(item: dict[str, Any]) -> float:
    if item["missing"] is not None:
        return 0.0
    if not item["verified"]:
        return settings.verify_low_confidence
    if item["verification_method"] == "user":
        return 1.0
    if item["verification_method"] == "exact":
        return max(_inherited_confidence(item), settings.ocr_confidence_high)
    return _inherited_confidence(item)  # semantic / llm — no promotion


def _review_reason(flags: dict[str, Any]) -> str | None:
    # Precedence order (SPEC-PHASE3.md Decision 4): missing > verification_failed >
    # high_stakes > unverified_source > low_confidence. transformed is never a reason.
    if flags["missing"] is not None:
        return flags["missing"]
    if flags["verification_failed"]:
        return "verification_failed"
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
        verification_failed = item["missing"] is None and not item["verified"]
        flags = {
            "missing": item["missing"],
            "verification_failed": verification_failed,
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
                "verified": item["verified"],
                "verification_method": item["verification_method"],
                "confidence": confidence,
                "confidence_band": _band(confidence),
                "needs_review": review_reason is not None,
                "review_reason": review_reason,
                "flags": flags,
            }
        )
    return results
