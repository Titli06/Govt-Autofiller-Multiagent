"""Confidence scorer is the safety-critical unit — verification > inherited trust >
missing, and high-stakes fields always route to review. Test these invariants first.

Phase 3's confidence_scorer_tool (app.agent.tools.confidence_scorer_tool) folds
document_verification_tool's result into the final score (SPEC-PHASE3.md §6.3): a
verified-exact match promotes to high confidence; a semantic/llm match keeps the
inherited score unchanged; a verification FAILURE overrides everything else (except
missing) and drops confidence to `verify_low_confidence`, flagged as the new
top-precedence review reason.
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
    verified=True,
    verification_method="exact",
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
        "verified": verified,
        "verification_method": verification_method,
    }


def test_missing_field_gets_zero_confidence_and_flagged():
    [result] = scorer.score(
        [
            _item(
                value=None,
                candidate_confidence=None,
                candidate_status=None,
                missing="no_candidate",
                verified=False,
                verification_method=None,
            )
        ]
    )
    assert result["confidence"] == 0.0
    assert result["confidence_band"] == "low"
    assert result["needs_review"] is True
    assert result["review_reason"] == "no_candidate"
    assert result["flags"]["verification_failed"] is False  # nothing to verify


def test_no_mapping_reason_surfaces_distinctly_from_no_candidate():
    [result] = scorer.score(
        [
            _item(
                value=None,
                candidate_confidence=None,
                candidate_status=None,
                missing="no_mapping",
                verified=False,
                verification_method=None,
            )
        ]
    )
    assert result["review_reason"] == "no_mapping"


def test_user_acted_candidate_verified_exact_gets_full_confidence():
    for status in ("user_confirmed", "user_corrected"):
        [result] = scorer.score(
            [_item(candidate_confidence=0.6, candidate_status=status, high_stakes=False)]
        )
        assert result["confidence"] == 1.0
        assert result["confidence_band"] == "high"


def test_exact_verification_promotes_low_inherited_confidence_to_high():
    [result] = scorer.score(
        [_item(candidate_confidence=0.5, candidate_status="confirmed", verification_method="exact")]
    )
    assert result["confidence"] == settings.ocr_confidence_high
    assert result["confidence_band"] == "high"


def test_semantic_verification_keeps_inherited_confidence_no_promotion():
    [result] = scorer.score(
        [_item(candidate_confidence=0.82, candidate_status="confirmed", verification_method="semantic")]
    )
    assert result["confidence"] == 0.82


def test_llm_verification_keeps_inherited_confidence_no_promotion():
    [result] = scorer.score(
        [_item(candidate_confidence=0.82, candidate_status="confirmed", verification_method="llm")]
    )
    assert result["confidence"] == 0.82


def test_user_correction_method_gives_full_confidence_regardless_of_inherited():
    [result] = scorer.score(
        [
            _item(
                candidate_confidence=0.4,
                candidate_status="confirmed",
                verification_method="user",
            )
        ]
    )
    assert result["confidence"] == 1.0
    assert result["confidence_band"] == "high"


def test_verification_failed_drops_confidence_to_verify_low_and_is_top_precedence():
    [result] = scorer.score(
        [
            _item(
                high_stakes=True,
                candidate_confidence=1.0,
                candidate_status="user_confirmed",
                verified=False,
                verification_method="llm",
            )
        ]
    )
    assert result["confidence"] == settings.verify_low_confidence
    assert result["confidence_band"] == "low"
    assert result["needs_review"] is True
    assert result["review_reason"] == "verification_failed"  # beats high_stakes
    assert result["flags"]["verification_failed"] is True


def test_band_thresholds_without_exact_promotion():
    # verification_method="semantic" so the promotion rule doesn't mask the raw
    # threshold behavior being tested here.
    high = scorer.score(
        [_item(candidate_confidence=settings.ocr_confidence_high, verification_method="semantic")]
    )[0]
    medium = scorer.score(
        [_item(candidate_confidence=settings.ocr_confidence_medium, verification_method="semantic")]
    )[0]
    low = scorer.score(
        [_item(candidate_confidence=settings.ocr_confidence_medium - 0.01, verification_method="semantic")]
    )[0]
    assert high["confidence_band"] == "high"
    assert medium["confidence_band"] == "medium"
    assert low["confidence_band"] == "low"


def test_confirmed_non_high_stakes_verified_exact_not_flagged():
    [result] = scorer.score(
        [_item(high_stakes=False, candidate_confidence=0.95, candidate_status="confirmed")]
    )
    assert result["needs_review"] is False
    assert result["review_reason"] is None


def test_high_stakes_verified_exact_still_flagged_for_review():
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
        [
            _item(
                high_stakes=False,
                candidate_confidence=0.5,
                candidate_status="confirmed",
                verification_method="semantic",  # no promotion, so 0.5 stays below threshold
            )
        ]
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
        [
            _item(
                value=None,
                candidate_confidence=None,
                candidate_status=None,
                missing="no_candidate",
                high_stakes=True,
                verified=False,
                verification_method=None,
            )
        ]
    )
    assert result["review_reason"] == "no_candidate"


def test_review_reason_precedence_verification_failed_beats_high_stakes():
    [result] = scorer.score(
        [_item(high_stakes=True, verified=False, verification_method="llm")]
    )
    assert result["review_reason"] == "verification_failed"


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


def test_verified_and_verification_method_pass_through_to_output():
    [result] = scorer.score([_item(verified=True, verification_method="exact")])
    assert result["verified"] is True
    assert result["verification_method"] == "exact"


# --- Phase 4: inferred-field cap + mandatory review (SPEC-PHASE4.md §6.6) --------------


def _inferred_item(*, mapping_cap=None, inferred=True, **overrides):
    item = _item(**overrides)
    item["mapping_cap"] = mapping_cap
    item["mapping_tier"] = "exact" if mapping_cap is not None else None
    item["inferred"] = inferred
    item["placement"] = {"page": 1, "bbox": [0.1, 0.2, 0.3, 0.25]}
    return item


def test_inferred_exact_verified_field_is_capped_below_high_band():
    [result] = scorer.score(
        [
            _inferred_item(
                mapping_cap=settings.map_cap_exact,
                candidate_confidence=0.5,
                candidate_status="confirmed",
                verification_method="exact",
                verified=True,
                high_stakes=False,
            )
        ]
    )
    # Without the cap this would promote to ocr_confidence_high (0.90) via the exact
    # promotion rule; the tier cap must win.
    assert result["confidence"] == settings.map_cap_exact
    assert result["confidence_band"] != "high"


def test_inferred_mapping_flags_every_inferred_field_even_when_verified_and_not_high_stakes():
    [result] = scorer.score(
        [
            _inferred_item(
                mapping_cap=settings.map_cap_exact,
                candidate_confidence=0.95,
                candidate_status="confirmed",
                verification_method="exact",
                verified=True,
                high_stakes=False,
            )
        ]
    )
    assert result["needs_review"] is True
    assert result["review_reason"] == "inferred_mapping"
    assert result["flags"]["inferred_mapping"] is True


def test_review_reason_precedence_inferred_mapping_beats_high_stakes():
    [result] = scorer.score(
        [
            _inferred_item(
                mapping_cap=settings.map_cap_exact,
                candidate_confidence=1.0,
                candidate_status="user_confirmed",
                verification_method="exact",
                verified=True,
                high_stakes=True,
            )
        ]
    )
    assert result["review_reason"] == "inferred_mapping"


def test_review_reason_precedence_verification_failed_beats_inferred_mapping():
    [result] = scorer.score(
        [
            _inferred_item(
                mapping_cap=settings.map_cap_exact,
                verified=False,
                verification_method="llm",
                high_stakes=False,
            )
        ]
    )
    assert result["review_reason"] == "verification_failed"


def test_no_mapping_inferred_field_still_flagged_inferred_but_missing_wins_reason():
    [result] = scorer.score(
        [
            _inferred_item(
                mapping_cap=None,  # no_mapping — no cap to apply
                value=None,
                candidate_confidence=None,
                candidate_status=None,
                missing="no_mapping",
                verified=False,
                verification_method=None,
                high_stakes=False,
            )
        ]
    )
    assert result["flags"]["inferred_mapping"] is True  # recorded in the audit trail
    assert result["review_reason"] == "no_mapping"  # but missing takes precedence
    assert result["confidence"] == 0.0  # no cap applied — mapping_cap was None


def test_template_field_never_flagged_inferred_mapping_regression():
    """A template field has no 'inferred'/'mapping_cap' keys at all (mirrors real
    profile_lookup_tool output for a template form) — Phase 3 behavior must be
    byte-for-byte unchanged."""
    [result] = scorer.score(
        [_item(high_stakes=False, candidate_confidence=0.95, candidate_status="confirmed")]
    )
    assert result["flags"]["inferred_mapping"] is False
    assert result["needs_review"] is False
    assert result["review_reason"] is None


def test_placement_and_mapping_tier_pass_through_to_output():
    [result] = scorer.score([_inferred_item(mapping_cap=settings.map_cap_strong)])
    assert result["mapping_tier"] == "exact"
    assert result["placement"] == {"page": 1, "bbox": [0.1, 0.2, 0.3, 0.25]}
