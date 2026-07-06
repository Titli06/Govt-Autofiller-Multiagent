"""Confidence scorer is the safety-critical unit — verified > inferred > missing,
and high-stakes fields always route to review. Test these invariants first.

Phase 2's confidence_scorer_tool (app.agent.tools.confidence_scorer_tool) scores the
provisional confidence of a filled form field (SPEC-PHASE2.md §6.4/§6.5), inherited
from the profile candidate that filled it — never a fresh verification (that's Phase 3).
"""

from __future__ import annotations

from app.agent.tools import confidence_scorer_tool as scorer
from app.config import settings


def _item(
    *,
    field_name="date_of_birth",
    profile_key="dob",
    value="12/04/1998",
    high_stakes=False,
    transformed=False,
    candidate_confidence=0.95,
    candidate_status="confirmed",
    missing=None,
):
    return {
        "field_name": field_name,
        "profile_key": profile_key,
        "value": value,
        "profile_field_id": "pf-1",
        "source_doc_id": "doc-1",
        "high_stakes": high_stakes,
        "transformed": transformed,
        "candidate_confidence": candidate_confidence,
        "candidate_status": candidate_status,
        "missing": missing,
    }


def test_missing_field_gets_zero_confidence_and_flagged():
    [result] = scorer.score([_item(value=None, candidate_confidence=None, candidate_status=None, missing="no_candidate")])
    assert result["confidence"] == 0.0
    assert result["confidence_band"] == "low"
    assert result["needs_review"] is True
    assert result["review_reason"] == "no_candidate"


def test_no_mapping_reason_surfaces_distinctly_from_no_candidate():
    [result] = scorer.score([_item(value=None, candidate_confidence=None, candidate_status=None, missing="no_mapping")])
    assert result["review_reason"] == "no_mapping"


def test_user_acted_candidate_gets_full_confidence():
    for status in ("user_confirmed", "user_corrected"):
        [result] = scorer.score([_item(candidate_confidence=0.6, candidate_status=status, high_stakes=False)])
        assert result["confidence"] == 1.0
        assert result["confidence_band"] == "high"


def test_confidence_otherwise_inherited_verbatim_from_candidate():
    [result] = scorer.score([_item(candidate_confidence=0.82, candidate_status="confirmed")])
    assert result["confidence"] == 0.82


def test_band_thresholds():
    high = scorer.score([_item(candidate_confidence=settings.ocr_confidence_high)])[0]
    medium = scorer.score([_item(candidate_confidence=settings.ocr_confidence_medium)])[0]
    low = scorer.score([_item(candidate_confidence=settings.ocr_confidence_medium - 0.01)])[0]
    assert high["confidence_band"] == "high"
    assert medium["confidence_band"] == "medium"
    assert low["confidence_band"] == "low"


def test_confirmed_non_high_stakes_high_confidence_not_flagged():
    [result] = scorer.score(
        [_item(high_stakes=False, candidate_confidence=0.95, candidate_status="confirmed")]
    )
    assert result["needs_review"] is False
    assert result["review_reason"] is None


def test_high_stakes_always_flagged_even_at_full_confidence():
    [result] = scorer.score(
        [_item(high_stakes=True, candidate_confidence=1.0, candidate_status="user_confirmed")]
    )
    assert result["needs_review"] is True
    assert result["review_reason"] == "high_stakes"


def test_unverified_source_flags_even_when_not_high_stakes_and_confidence_high():
    [result] = scorer.score(
        [_item(high_stakes=False, candidate_confidence=0.96, candidate_status="needs_confirmation")]
    )
    assert result["needs_review"] is True
    assert result["review_reason"] == "unverified_source"
    assert result["flags"]["unverified_source"] is True


def test_failed_validation_candidate_counts_as_unverified_source():
    [result] = scorer.score(
        [_item(high_stakes=False, candidate_confidence=0.4, candidate_status="failed_validation")]
    )
    assert result["flags"]["unverified_source"] is True


def test_low_confidence_alone_flags_review():
    [result] = scorer.score(
        [_item(high_stakes=False, candidate_confidence=0.5, candidate_status="confirmed")]
    )
    assert result["needs_review"] is True
    assert result["review_reason"] == "low_confidence"


def test_transformed_alone_never_flags_review():
    [result] = scorer.score(
        [
            _item(
                high_stakes=False,
                transformed=True,
                candidate_confidence=0.95,
                candidate_status="confirmed",
            )
        ]
    )
    assert result["flags"]["transformed"] is True
    assert result["needs_review"] is False
    assert result["review_reason"] is None


def test_review_reason_precedence_missing_beats_everything():
    [result] = scorer.score(
        [_item(value=None, candidate_confidence=None, candidate_status=None, missing="no_candidate", high_stakes=True)]
    )
    assert result["review_reason"] == "no_candidate"


def test_review_reason_precedence_high_stakes_beats_unverified_and_low_confidence():
    [result] = scorer.score(
        [_item(high_stakes=True, candidate_confidence=0.3, candidate_status="needs_confirmation")]
    )
    assert result["review_reason"] == "high_stakes"


def test_review_reason_precedence_unverified_beats_low_confidence():
    [result] = scorer.score(
        [_item(high_stakes=False, candidate_confidence=0.3, candidate_status="needs_confirmation")]
    )
    assert result["review_reason"] == "unverified_source"


def test_verified_is_always_false_in_phase_2():
    [result] = scorer.score([_item()])
    assert result["verified"] is False
