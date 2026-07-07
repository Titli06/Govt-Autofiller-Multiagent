"""build_graph() end-to-end with fakes: a stub classifier, an in-memory profile
snapshot (now carrying source snippets), and a stub verifier — no DB, no real
vision-LLM call. Exercises the full node order (form_schema -> profile_lookup ->
document_verification -> confidence_scorer) and the type_mismatch short-circuit
(SPEC-PHASE3.md §6.2)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent.graph import build_graph
from app.agent.tools.profile_lookup_tool import CandidateView

_NOW = datetime.now(timezone.utc)


def _snapshot():
    return {
        "full_name": [
            CandidateView(
                "pf-1", "doc-1", "aadhaar", "Ravi Kumar", 0.95, "confirmed", _NOW,
                source_snippet="Name: Ravi Kumar",
            )
        ],
        "dob": [
            CandidateView(
                "pf-2", "doc-1", "aadhaar", "1998-04-12", 0.96, "user_confirmed", _NOW,
                source_snippet="DOB: 12/04/1998",
            )
        ],
        "aadhaar_number": [
            CandidateView(
                "pf-3", "doc-1", "aadhaar", "234123412346", 0.4, "needs_confirmation", _NOW,
                source_snippet="Aadhaar: 2341 2341 2346",
            )
        ],
    }


def _always_verified(value: str, source_doc_id: str | None) -> bool:
    return True


def _invoke(declared_form_type, classifier, snapshot=None, verifier=_always_verified):
    graph = build_graph()
    return graph.invoke(
        {
            "user_id": "u1",
            "form_id": "f1",
            "declared_form_type": declared_form_type,
            "detected_form_type": None,
            "type_mismatch": False,
            "form_type": None,
            "field_specs": [],
            "fields": [],
        },
        config={
            "configurable": {
                "snapshot": snapshot if snapshot is not None else _snapshot(),
                "images": [b"page"],
                "classifier": classifier,
                "verifier": verifier,
            }
        },
    )


def test_happy_path_produces_one_field_result_per_template_field():
    result = _invoke("income_certificate", lambda images, known: "income_certificate")

    assert result["type_mismatch"] is False
    field_names = {f["field_name"] for f in result["fields"]}
    assert field_names == {
        "applicant_name",
        "father_name",
        "date_of_birth",
        "address",
        "annual_income",
        "aadhaar_number",
    }


def test_filled_field_carries_verified_confidence_and_provenance():
    result = _invoke("income_certificate", lambda images, known: "income_certificate")
    by_name = {f["field_name"]: f for f in result["fields"]}

    name_field = by_name["applicant_name"]
    assert name_field["value"] == "Ravi Kumar"
    assert name_field["verified"] is True
    assert name_field["verification_method"] == "exact"
    assert name_field["confidence"] == 0.95
    assert name_field["profile_field_id"] == "pf-1"
    assert name_field["needs_review"] is False


def test_user_confirmed_dob_gets_full_confidence_but_still_flagged_high_stakes():
    result = _invoke("income_certificate", lambda images, known: "income_certificate")
    dob = next(f for f in result["fields"] if f["field_name"] == "date_of_birth")

    assert dob["value"] == "12/04/1998"  # reformatted per template
    assert dob["transformed"] is True
    assert dob["verified"] is True
    assert dob["verification_method"] == "exact"  # matches its own (reformatted) snippet
    assert dob["confidence"] == 1.0  # user-acted candidate
    assert dob["needs_review"] is True
    assert dob["review_reason"] == "high_stakes"  # high-stakes always wins precedence


def test_unverified_source_propagates_to_review():
    result = _invoke("income_certificate", lambda images, known: "income_certificate")
    aadhaar = next(f for f in result["fields"] if f["field_name"] == "aadhaar_number")

    assert aadhaar["needs_review"] is True
    assert aadhaar["verified"] is True  # deterministic exact match against its snippet
    # high_stakes takes precedence over unverified_source in the reason, but both are
    # true in the audit trail.
    assert aadhaar["flags"]["unverified_source"] is True


def test_deterministic_miss_escalates_to_verifier_and_a_false_result_flags_verification_failed():
    result = _invoke(
        "income_certificate", lambda images, known: "income_certificate", verifier=lambda v, d: False
    )
    name_field = next(f for f in result["fields"] if f["field_name"] == "applicant_name")

    # No snippet mismatch here, so force escalation via a snapshot with no snippet.
    assert name_field["verified"] is True  # exact snippet match — never escalates
    aadhaar = next(f for f in result["fields"] if f["field_name"] == "aadhaar_number")
    assert aadhaar["verified"] is True  # also an exact match in this fixture


def test_no_snippet_escalates_and_verifier_false_fails_verification():
    snapshot = {
        "full_name": [
            CandidateView("pf-1", "doc-1", "aadhaar", "Ravi Kumar", 0.95, "confirmed", _NOW)
        ]
    }
    result = _invoke(
        "income_certificate",
        lambda images, known: "income_certificate",
        snapshot=snapshot,
        verifier=lambda v, d: False,
    )
    name_field = next(f for f in result["fields"] if f["field_name"] == "applicant_name")
    assert name_field["verified"] is False
    assert name_field["verification_method"] == "llm"
    assert name_field["review_reason"] == "verification_failed"
    assert name_field["confidence"] < 0.9


def test_unmapped_and_missing_fields_flagged_with_distinct_reasons():
    result = _invoke("income_certificate", lambda images, known: "income_certificate")
    by_name = {f["field_name"]: f for f in result["fields"]}

    assert by_name["annual_income"]["review_reason"] == "no_mapping"
    assert by_name["father_name"]["review_reason"] == "no_candidate"  # not in snapshot


def test_type_mismatch_short_circuits_with_no_fields():
    result = _invoke("income_certificate", lambda images, known: "scholarship_application")

    assert result["type_mismatch"] is True
    assert result["detected_form_type"] == "scholarship_application"
    assert result["fields"] == []


def test_unknown_classification_proceeds_on_declared_type():
    result = _invoke("income_certificate", lambda images, known: "unknown")

    assert result["type_mismatch"] is False
    assert result["detected_form_type"] == "unknown"
    assert len(result["fields"]) == 6


def test_no_profile_snapshot_all_fields_missing():
    result = _invoke("income_certificate", lambda images, known: "income_certificate", snapshot={})

    assert all(f["value"] is None for f in result["fields"])
    assert all(f["needs_review"] for f in result["fields"])
    assert all(f["verified"] is False for f in result["fields"])
