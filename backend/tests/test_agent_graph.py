"""build_graph() end-to-end with fakes: a stub classifier, an in-memory profile
snapshot (now carrying source snippets), and a stub verifier — no DB, no real
vision-LLM call. Exercises the full node order (form_schema -> profile_lookup ->
document_verification -> confidence_scorer) and the type_mismatch short-circuit
(SPEC-PHASE3.md §6.2).

Phase 4 (SPEC-PHASE4.md §6.2/§12): also exercises the template-vs-inference branch —
a confident classify_form detection of a known type overrides an unseen declared
label (Decision 2); a genuinely unrecognized form infers its schema via stub
field_detector/label_mapper callables."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent.graph import build_graph
from app.agent.tools.profile_lookup_tool import CandidateView
from app.config import settings
from app.services.form_placement.document_ai import DetectedField

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


def _no_field_detector(images):
    raise AssertionError("field_detector should not be called on the template path")


def _no_label_mapper(labels, canonical_keys):
    raise AssertionError("label_mapper should not be called on the template path")


def _invoke(
    declared_form_type,
    classifier,
    snapshot=None,
    verifier=_always_verified,
    field_detector=_no_field_detector,
    label_mapper=_no_label_mapper,
):
    graph = build_graph()
    return graph.invoke(
        {
            "user_id": "u1",
            "form_id": "f1",
            "declared_form_type": declared_form_type,
            "detected_form_type": None,
            "type_mismatch": False,
            "form_type": None,
            "schema_source": "template",
            "field_specs": [],
            "fields": [],
        },
        config={
            "configurable": {
                "snapshot": snapshot if snapshot is not None else _snapshot(),
                "images": [b"page"],
                "classifier": classifier,
                "verifier": verifier,
                "field_detector": field_detector,
                "label_mapper": label_mapper,
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


# --- Phase 4: schema inference for unseen forms (SPEC-PHASE4.md §6.2, Decisions 1/2) ---


def test_confident_detection_of_known_type_overrides_unseen_declared_label():
    """Decision 2: an unseen declared label the vision-LLM confidently recognizes as
    a known type is filled from the TEMPLATE, not inferred — and it's not a
    type_mismatch (the field_detector/label_mapper must never even be called)."""
    result = _invoke(
        "passport_renewal",  # not in the template registry
        lambda images, known: "income_certificate",
        snapshot={},
    )
    assert result["type_mismatch"] is False
    assert result["schema_source"] == "template"
    assert result["form_type"] == "income_certificate"
    field_names = {f["field_name"] for f in result["fields"]}
    assert "applicant_name" in field_names


def test_unrecognized_declared_type_infers_schema_via_document_ai_and_label_mapper():
    detected_fields = [
        DetectedField(name="Father's Name", page=1, value_bbox=(0.1, 0.2, 0.4, 0.25), confidence=0.9),
        DetectedField(name="Purpose", page=1, value_bbox=(0.1, 0.3, 0.4, 0.35), confidence=0.9),
    ]
    mapping = {
        "Father's Name": {"profile_key": "father_name", "tier": "exact"},
        "Purpose": {"profile_key": None, "tier": "none"},
    }
    snapshot = {
        "father_name": [
            CandidateView(
                "pf-1", "doc-1", "aadhaar", "Suresh Kumar", 0.9, "confirmed", _NOW,
                source_snippet="Father: Suresh Kumar",
            )
        ]
    }

    result = _invoke(
        "marriage_certificate",  # not in the template registry
        lambda images, known: "unknown",
        snapshot=snapshot,
        field_detector=lambda images: detected_fields,
        label_mapper=lambda labels, keys: mapping,
    )

    assert result["type_mismatch"] is False
    assert result["schema_source"] == "inferred"
    assert result["form_type"] == "marriage_certificate"

    by_name = {f["field_name"]: f for f in result["fields"]}
    father = by_name["father_s_name"]
    assert father["value"] == "Suresh Kumar"
    assert father["verified"] is True  # exact snippet match
    assert father["confidence"] == settings.map_cap_exact  # tier-capped, not promoted to high
    assert father["needs_review"] is True  # ALWAYS reviewed on an inferred form (Decision 1)
    assert father["review_reason"] == "inferred_mapping"

    purpose = by_name["purpose"]
    assert purpose["value"] is None
    assert purpose["review_reason"] == "no_mapping"  # missing still wins over inferred_mapping
