"""Deterministic format validators (§3.3 of SPEC-PHASE1.md) — the grounding layer that
sets confidence instead of trusting the LLM's self-report."""

from __future__ import annotations

from app.core.validators import (
    is_valid_aadhaar,
    is_valid_pan,
    normalize_aadhaar,
    normalize_dob,
    normalize_gender,
    normalize_pan,
    parse_dob,
    snippet_contains,
)

# A real Verhoeff-valid 12-digit test number.
VALID_AADHAAR = "234123412346"


def test_valid_pan_accepted():
    assert is_valid_pan("ABCDE1234F")
    assert is_valid_pan("abcde1234f")  # normalized before check
    assert is_valid_pan("ABCDE 1234 F".replace(" ", ""))


def test_invalid_pan_rejected():
    assert not is_valid_pan("ABCDE12345")  # last char must be a letter
    assert not is_valid_pan("12345ABCDF")  # wrong layout
    assert not is_valid_pan("ABCD1234F")  # too short


def test_normalize_pan():
    assert normalize_pan(" abcde1234f ") == "ABCDE1234F"


def test_valid_aadhaar_accepted():
    assert is_valid_aadhaar(VALID_AADHAAR)
    assert is_valid_aadhaar("2341 2341 2346")  # spaced form


def test_invalid_aadhaar_wrong_length_rejected():
    assert not is_valid_aadhaar("12345")
    assert not is_valid_aadhaar("1234123412345")  # 13 digits


def test_invalid_aadhaar_bad_checksum_rejected():
    # Flip the last digit of a valid number -> checksum should now fail.
    bad = VALID_AADHAAR[:-1] + str((int(VALID_AADHAAR[-1]) + 1) % 10)
    assert not is_valid_aadhaar(bad)


def test_normalize_aadhaar_strips_non_digits():
    assert normalize_aadhaar("2341-2341-2346") == VALID_AADHAAR


def test_parse_dob_multiple_formats():
    assert parse_dob("1998-04-12").isoformat() == "1998-04-12"
    assert parse_dob("12/04/1998").isoformat() == "1998-04-12"
    assert parse_dob("12-04-1998").isoformat() == "1998-04-12"


def test_parse_dob_implausible_year_rejected():
    assert parse_dob("12/04/1850") is None  # before 1900
    assert parse_dob("12/04/2999") is None  # future


def test_parse_dob_garbage_returns_none():
    assert parse_dob("not a date") is None


def test_normalize_dob_returns_iso_string():
    assert normalize_dob("12/04/1998") == "1998-04-12"
    assert normalize_dob("garbage") is None


def test_normalize_gender_variants():
    assert normalize_gender("M") == "Male"
    assert normalize_gender("male") == "Male"
    assert normalize_gender("F") == "Female"
    assert normalize_gender("पुरुष") == "Male"
    assert normalize_gender("महिला") == "Female"
    assert normalize_gender("unknown-value") is None


def test_snippet_contains_exact():
    assert snippet_contains("ABCDE1234F", "Permanent Account Number ABCDE1234F")


def test_snippet_contains_case_and_whitespace_insensitive():
    assert snippet_contains("Rajesh Kumar", "Name:   rajesh   kumar")


def test_snippet_contains_ignores_digit_grouping():
    assert snippet_contains("234123412346", "Aadhaar: 2341 2341 2346")
    assert snippet_contains("2341 2341 2346", "Aadhaar: 234123412346")


def test_snippet_contains_false_when_absent():
    assert not snippet_contains("Rajesh Kumar", "Father's Name: Suresh Kumar")


def test_snippet_contains_empty_inputs_false():
    assert not snippet_contains("", "some snippet")
    assert not snippet_contains("value", "")
