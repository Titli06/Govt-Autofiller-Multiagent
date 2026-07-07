"""profile_lookup_tool: deterministic profile_key mapping, candidate selection
(user-acted > confidence > recency), and format transforms (SPEC-PHASE2.md Decisions
2-4). Pure — no DB, no snapshot builder involved."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.agent.tools.form_schema_tool import TemplateField
from app.agent.tools.profile_lookup_tool import CandidateView, apply_format, lookup

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candidate(
    value="Ravi Kumar", confidence=0.9, status="confirmed", created_at=None, doc="doc-1", snippet=None
):
    return CandidateView(
        profile_field_id=f"pf-{value}",
        source_doc_id=doc,
        doc_type="aadhaar",
        value=value,
        confidence=confidence,
        status=status,
        created_at=created_at or _NOW,
        source_snippet=snippet,
    )


# --- apply_format --------------------------------------------------------------------


def test_as_is_leaves_value_unchanged():
    value, transformed = apply_format("Ravi Kumar", "as_is")
    assert value == "Ravi Kumar"
    assert transformed is False


def test_upper_transforms_and_flags():
    value, transformed = apply_format("Ravi Kumar", "upper")
    assert value == "RAVI KUMAR"
    assert transformed is True


def test_upper_already_upper_not_flagged_transformed():
    value, transformed = apply_format("RAVI KUMAR", "upper")
    assert transformed is False


def test_single_line_collapses_whitespace():
    value, transformed = apply_format("123 MG Road\nBengaluru", "single_line")
    assert value == "123 MG Road Bengaluru"
    assert transformed is True


def test_date_format_reformats_iso_to_target():
    value, transformed = apply_format("1998-04-12", "date:%d/%m/%Y")
    assert value == "12/04/1998"
    assert transformed is True


def test_date_format_unparsable_left_verbatim_not_flagged():
    value, transformed = apply_format("not-a-date", "date:%d/%m/%Y")
    assert value == "not-a-date"
    assert transformed is False


# --- candidate selection (Decision 3) -------------------------------------------------


def test_no_mapping_when_profile_key_is_null():
    spec = TemplateField(name="annual_income", profile_key=None, high_stakes=True)
    [result] = lookup([spec], snapshot={})
    assert result["value"] is None
    assert result["missing"] == "no_mapping"
    assert result["candidate_snippet"] is None


def test_no_candidate_when_profile_has_no_value_for_key():
    spec = TemplateField(name="aadhaar_number", profile_key="aadhaar_number", high_stakes=True)
    [result] = lookup([spec], snapshot={})
    assert result["value"] is None
    assert result["missing"] == "no_candidate"
    assert result["candidate_snippet"] is None


def test_candidate_snippet_carried_through_for_verification():
    spec = TemplateField(name="applicant_name", profile_key="full_name", high_stakes=False)
    snapshot = {"full_name": [_candidate("Ravi Kumar", snippet="Name: Ravi Kumar")]}
    [result] = lookup([spec], snapshot)
    assert result["candidate_snippet"] == "Name: Ravi Kumar"


def test_candidate_snippet_none_when_candidate_has_no_snippet():
    spec = TemplateField(name="applicant_name", profile_key="full_name", high_stakes=False)
    snapshot = {"full_name": [_candidate("Ravi Kumar")]}
    [result] = lookup([spec], snapshot)
    assert result["candidate_snippet"] is None


def test_single_candidate_fills_directly():
    spec = TemplateField(name="applicant_name", profile_key="full_name", high_stakes=False)
    snapshot = {"full_name": [_candidate("Ravi Kumar")]}
    [result] = lookup([spec], snapshot)
    assert result["value"] == "Ravi Kumar"
    assert result["missing"] is None
    assert result["profile_field_id"] == "pf-Ravi Kumar"


def test_user_acted_candidate_wins_over_higher_confidence_non_acted():
    spec = TemplateField(name="applicant_name", profile_key="full_name", high_stakes=False)
    snapshot = {
        "full_name": [
            _candidate("Auto Extracted", confidence=0.99, status="confirmed"),
            _candidate("User Corrected", confidence=0.5, status="user_corrected"),
        ]
    }
    [result] = lookup([spec], snapshot)
    assert result["value"] == "User Corrected"


def test_higher_confidence_wins_when_neither_user_acted():
    spec = TemplateField(name="applicant_name", profile_key="full_name", high_stakes=False)
    snapshot = {
        "full_name": [
            _candidate("Lower Conf", confidence=0.7, status="confirmed"),
            _candidate("Higher Conf", confidence=0.95, status="needs_confirmation"),
        ]
    }
    [result] = lookup([spec], snapshot)
    assert result["value"] == "Higher Conf"


def test_most_recent_wins_on_tiebreak():
    spec = TemplateField(name="applicant_name", profile_key="full_name", high_stakes=False)
    snapshot = {
        "full_name": [
            _candidate("Older", confidence=0.9, status="confirmed", created_at=_NOW - timedelta(days=1)),
            _candidate("Newer", confidence=0.9, status="confirmed", created_at=_NOW),
        ]
    }
    [result] = lookup([spec], snapshot)
    assert result["value"] == "Newer"


def test_format_applied_and_transformed_flag_set():
    spec = TemplateField(name="date_of_birth", profile_key="dob", high_stakes=True, format="date:%d/%m/%Y")
    snapshot = {"dob": [_candidate("1998-04-12")]}
    [result] = lookup([spec], snapshot)
    assert result["value"] == "12/04/1998"
    assert result["transformed"] is True


def test_high_stakes_and_provenance_carried_through():
    spec = TemplateField(name="aadhaar_number", profile_key="aadhaar_number", high_stakes=True)
    snapshot = {"aadhaar_number": [_candidate("234123412346", doc="doc-42")]}
    [result] = lookup([spec], snapshot)
    assert result["high_stakes"] is True
    assert result["source_doc_id"] == "doc-42"
