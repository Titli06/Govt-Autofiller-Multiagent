"""document_verification_tool: the hybrid deterministic-first, LLM-escalation trust
layer (SPEC-PHASE3.md §3). Pure — no DB/crypto; the vision-LLM path is a stub
callable so these tests never hit the network."""

from __future__ import annotations

from app.agent.tools.document_verification_tool import deterministic_match, verify


def _item(
    *,
    field_name="applicant_name",
    profile_key="full_name",
    value="Ravi Kumar",
    snippet="Name: Ravi Kumar",
    missing=None,
):
    return {
        "field_name": field_name,
        "profile_key": profile_key,
        "value": value,
        "profile_field_id": "pf-1",
        "source_doc_id": "doc-1",
        "high_stakes": False,
        "transformed": False,
        "candidate_snippet": snippet,
        "missing": missing,
    }


# --- deterministic_match ---------------------------------------------------------


def test_exact_snippet_match_free_text():
    assert deterministic_match("full_name", "Ravi Kumar", "Name: Ravi Kumar") == "exact"


def test_free_text_no_semantic_tier_misses_when_not_contained():
    assert deterministic_match("full_name", "Someone Else", "Name: Ravi Kumar") == "miss"


def test_empty_snippet_always_misses():
    assert deterministic_match("full_name", "Ravi Kumar", "") == "miss"
    assert deterministic_match("full_name", "Ravi Kumar", None) == "miss"


def test_upper_transformed_value_still_exact_against_original_snippet():
    # snippet_contains is casefold-normalized, so an "upper" format transform still matches.
    assert deterministic_match("full_name", "RAVI KUMAR", "Name: Ravi Kumar") == "exact"


def test_dob_exact_when_snippet_contains_the_formatted_value():
    assert deterministic_match("dob", "12/04/1998", "DOB: 12/04/1998") == "exact"


def test_dob_swapped_format_bug_is_a_deterministic_miss():
    # The form value is DD/MM but was actually built from a MM/DD misread — it no
    # longer matches its own snippet, and parses to a DIFFERENT calendar day, so this
    # must miss deterministically (catching exactly this class of bug is the point).
    assert deterministic_match("dob", "04/12/1998", "DOB: 12/04/1998") == "miss"


def test_dob_semantic_when_snippet_uses_a_different_but_equal_date_format():
    assert deterministic_match("dob", "12/04/1998", "Date of Birth: 12 April 1998") == "semantic"


def test_dob_miss_when_neither_value_nor_snippet_parses():
    assert deterministic_match("dob", "not-a-date", "also not a date") == "miss"


def test_aadhaar_exact_when_snippet_contains_formatted_value():
    assert deterministic_match("aadhaar_number", "234123412346", "Aadhaar: 2341 2341 2346") == "exact"


def test_aadhaar_semantic_when_normalized_value_in_normalized_snippet():
    # Digits-only value; snippet has the same digits with different (or no) spacing —
    # snippet_contains handles the exact case, so exercise the semantic path with a
    # snippet that only contains the digits at all after normalization.
    assert deterministic_match("aadhaar_number", "2341-2341-2346", "234123412346") == "semantic"


def test_pan_semantic_normalization():
    assert deterministic_match("pan_number", "abcde1234f", "PAN No: ABCDE1234F") == "exact"


def test_id_miss_when_normalized_value_absent_from_snippet():
    assert deterministic_match("aadhaar_number", "111122223333", "234123412346") == "miss"


# --- verify() ----------------------------------------------------------------------


def test_missing_field_is_left_unverified_no_verifier_call():
    calls = []
    [result] = verify([_item(missing="no_candidate", value=None, snippet=None)], lambda v, d: calls.append(1) or True)
    assert result["verified"] is False
    assert result["verification_method"] is None
    assert calls == []


def test_exact_match_verified_without_verifier_call():
    calls = []
    [result] = verify([_item()], lambda v, d: calls.append(1) or True)
    assert result["verified"] is True
    assert result["verification_method"] == "exact"
    assert calls == []  # deterministic pass never calls the LLM


def test_semantic_match_verified_without_verifier_call():
    calls = []
    [result] = verify(
        [_item(profile_key="dob", value="12/04/1998", snippet="Date of Birth: 12 April 1998")],
        lambda v, d: calls.append(1) or True,
    )
    assert result["verified"] is True
    assert result["verification_method"] == "semantic"
    assert calls == []


def test_deterministic_miss_escalates_and_verifier_true_gives_llm_verified():
    [result] = verify([_item(value="Someone Else")], lambda v, d: True)
    assert result["verified"] is True
    assert result["verification_method"] == "llm"


def test_deterministic_miss_escalates_and_verifier_false_gives_verification_failed():
    [result] = verify([_item(value="Someone Else")], lambda v, d: False)
    assert result["verified"] is False
    assert result["verification_method"] == "llm"


def test_empty_snippet_escalates_to_verifier():
    calls = []

    def verifier(value, doc_id):
        calls.append((value, doc_id))
        return True

    [result] = verify([_item(snippet=None)], verifier)
    assert calls == [("Ravi Kumar", "doc-1")]
    assert result["verified"] is True
    assert result["verification_method"] == "llm"
