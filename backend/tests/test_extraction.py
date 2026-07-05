"""Grounding is the safety-critical piece of Phase 1 (mirrors the confidence_scorer_tool
test file docstring pattern): confidence must come from the snippet + format check, not
the model's self-report. vision_llm.extract is mocked — no real API calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.extraction import extract_profile_fields
from app.services.ocr.vision_llm import RawExtraction, RawField


def _raw(detected_doc_type, **fields):
    return RawExtraction(detected_doc_type=detected_doc_type, fields=fields)


def _field(value, snippet, present=True, self_confidence=0.5):
    return RawField(value=value, source_snippet=snippet, self_confidence=self_confidence, present=present)


def _extract_with(raw_extraction):
    with patch("app.services.extraction.vision_extract", return_value=raw_extraction):
        return extract_profile_fields([b"fake-image"], "aadhaar")


def test_type_mismatch_flags_and_writes_no_fields():
    raw = _raw("pan", full_name=_field("Rajesh Kumar", "Name: Rajesh Kumar"))
    result = _extract_with(raw)
    assert result.type_mismatch is True
    assert result.detected_doc_type == "pan"
    assert result.fields == []


def test_matching_doc_type_proceeds():
    raw = _raw(
        "aadhaar",
        full_name=_field("Rajesh Kumar", "Name: Rajesh Kumar"),
        dob=_field("1998-04-12", "DOB: 1998-04-12"),
        gender=_field("Male", "Gender: Male"),
        aadhaar_number=_field("234123412346", "Aadhaar: 2341 2341 2346"),
        address=_field("123 MG Road", "Address: 123 MG Road"),
    )
    result = _extract_with(raw)
    assert result.type_mismatch is False
    assert result.missing_fields == []
    assert len(result.fields) == 5


def test_typed_field_snippet_match_and_valid_format_scores_high():
    raw = _raw(
        "aadhaar",
        aadhaar_number=_field("234123412346", "Aadhaar Number: 2341 2341 2346", self_confidence=0.99),
    )
    result = _extract_with(raw)
    f = result.fields[0]
    assert f.confidence_band == "high"
    assert 0.95 <= f.confidence <= 0.97
    assert f.validators == {"snippet_contains": True, "format_valid": True, "normalized": False}


def test_free_text_field_snippet_match_scores_medium():
    raw = _raw("aadhaar", full_name=_field("Rajesh Kumar", "Name: Rajesh Kumar", self_confidence=0.5))
    result = _extract_with(raw)
    f = result.fields[0]
    assert f.confidence_band == "medium"
    assert 0.80 <= f.confidence <= 0.88


def test_free_text_field_high_self_report_can_reach_high_band():
    raw = _raw("aadhaar", full_name=_field("Rajesh Kumar", "Name: Rajesh Kumar", self_confidence=0.99))
    result = _extract_with(raw)
    f = result.fields[0]
    assert f.confidence >= 0.90
    assert f.confidence_band == "high"


def test_normalization_caps_confidence_at_medium():
    # DOB given in dd/mm/yyyy has to be reformatted to ISO -- a real conversion risk.
    raw = _raw("aadhaar", dob=_field("12/04/1998", "Date of Birth: 12/04/1998", self_confidence=0.9))
    result = _extract_with(raw)
    f = result.fields[0]
    assert f.value == "1998-04-12"
    assert f.confidence <= 0.85
    assert f.confidence_band == "medium"
    assert f.validators["normalized"] is True


def test_value_not_in_snippet_scores_low():
    raw = _raw(
        "aadhaar",
        full_name=_field("Rajesh Kumar", "Father's Name: Suresh Kumar", self_confidence=0.95),
    )
    result = _extract_with(raw)
    f = result.fields[0]
    assert f.confidence <= 0.55
    assert f.confidence_band == "low"
    assert f.validators["snippet_contains"] is False


def test_format_invalid_typed_field_scores_very_low():
    # 11 digits, not a valid Aadhaar number at all.
    raw = _raw(
        "aadhaar",
        aadhaar_number=_field("12345678901", "Aadhaar: 12345678901", self_confidence=0.9),
    )
    result = _extract_with(raw)
    f = result.fields[0]
    assert f.confidence <= 0.40
    assert f.confidence_band == "low"
    assert f.format_valid is False


def test_absent_field_is_reported_missing_not_written():
    raw = _raw("aadhaar", aadhaar_number=_field(None, None, present=False))
    result = _extract_with(raw)
    assert result.fields == []
    assert "aadhaar_number" in result.missing_fields


def test_high_stakes_fields_flagged():
    raw = _raw(
        "aadhaar",
        aadhaar_number=_field("234123412346", "Aadhaar: 2341 2341 2346"),
        dob=_field("1998-04-12", "DOB: 1998-04-12"),
        full_name=_field("Rajesh Kumar", "Name: Rajesh Kumar"),
    )
    result = _extract_with(raw)
    by_name = {f.field_name: f for f in result.fields}
    assert by_name["aadhaar_number"].high_stakes is True
    assert by_name["dob"].high_stakes is True
    assert by_name["full_name"].high_stakes is False


@pytest.mark.parametrize("self_confidence", [0.0, 0.5, 1.0])
def test_confidence_always_in_valid_range(self_confidence):
    raw = _raw(
        "aadhaar",
        full_name=_field("Rajesh Kumar", "Name: Rajesh Kumar", self_confidence=self_confidence),
    )
    result = _extract_with(raw)
    assert 0.0 <= result.fields[0].confidence <= 1.0
