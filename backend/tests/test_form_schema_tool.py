"""Template registry: loads/validates the real templates, and rejects malformed ones
at load time (fail fast, not mid-fill — SPEC-PHASE2.md §11). Also covers the
declared/detected form-type mismatch decision (Decision 1)."""

from __future__ import annotations

import json

import pytest

from app.agent.tools.form_schema_tool import (
    TemplateError,
    known_types,
    load_registry,
    load_template,
    resolve_form_type,
)


def _write(tmp_path, name, data):
    (tmp_path / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def _valid_field(**overrides):
    field = {"name": "applicant_name", "profile_key": "full_name", "high_stakes": False}
    field.update(overrides)
    return field


# --- real templates ---------------------------------------------------------------


def test_real_registry_loads_both_known_templates():
    registry = load_registry()
    assert set(registry.keys()) == {"income_certificate", "scholarship_application"}


def test_known_types_sorted():
    assert known_types() == ["income_certificate", "scholarship_application"]


def test_load_template_income_certificate_fields():
    template = load_template("income_certificate")
    names = [f.name for f in template.required_fields]
    assert "applicant_name" in names
    assert "annual_income" in names
    income_field = next(f for f in template.required_fields if f.name == "annual_income")
    assert income_field.profile_key is None
    assert income_field.high_stakes is True
    dob_field = next(f for f in template.required_fields if f.name == "date_of_birth")
    assert dob_field.format == "date:%d/%m/%Y"


def test_load_unknown_form_type_raises():
    with pytest.raises(TemplateError):
        load_template("passport_renewal")


# --- malformed templates (fail fast) -----------------------------------------------


def test_form_type_must_match_filename_stem(tmp_path):
    _write(tmp_path, "income_certificate", {
        "form_type": "wrong_name",
        "display_name": "X",
        "required_fields": [_valid_field()],
    })
    with pytest.raises(TemplateError, match="form_type must match filename stem"):
        load_registry(tmp_path)


def test_unknown_profile_key_rejected(tmp_path):
    _write(tmp_path, "some_form", {
        "form_type": "some_form",
        "display_name": "X",
        "required_fields": [_valid_field(profile_key="not_a_real_key")],
    })
    with pytest.raises(TemplateError, match="unknown profile_key"):
        load_registry(tmp_path)


def test_null_profile_key_is_allowed(tmp_path):
    _write(tmp_path, "some_form", {
        "form_type": "some_form",
        "display_name": "X",
        "required_fields": [_valid_field(profile_key=None)],
    })
    registry = load_registry(tmp_path)
    assert registry["some_form"].required_fields[0].profile_key is None


def test_unsupported_format_grammar_rejected(tmp_path):
    _write(tmp_path, "some_form", {
        "form_type": "some_form",
        "display_name": "X",
        "required_fields": [_valid_field(format="not_a_real_format")],
    })
    with pytest.raises(TemplateError, match="unsupported format grammar"):
        load_registry(tmp_path)


def test_date_format_grammar_accepted(tmp_path):
    _write(tmp_path, "some_form", {
        "form_type": "some_form",
        "display_name": "X",
        "required_fields": [_valid_field(format="date:%d/%m/%Y")],
    })
    registry = load_registry(tmp_path)
    assert registry["some_form"].required_fields[0].format == "date:%d/%m/%Y"


def test_empty_required_fields_rejected(tmp_path):
    _write(tmp_path, "some_form", {
        "form_type": "some_form",
        "display_name": "X",
        "required_fields": [],
    })
    with pytest.raises(TemplateError, match="non-empty"):
        load_registry(tmp_path)


def test_invalid_json_rejected(tmp_path):
    (tmp_path / "some_form.json").write_text("not json", encoding="utf-8")
    with pytest.raises(TemplateError, match="invalid JSON"):
        load_registry(tmp_path)


def test_empty_templates_dir_rejected(tmp_path):
    with pytest.raises(TemplateError, match="no templates found"):
        load_registry(tmp_path)


# --- mismatch resolution (Decision 1) -----------------------------------------------


def test_resolve_no_detection_defers_to_declared():
    resolved, mismatch = resolve_form_type("income_certificate", None)
    assert resolved == "income_certificate"
    assert mismatch is False


def test_resolve_matching_detection_no_mismatch():
    resolved, mismatch = resolve_form_type("income_certificate", "income_certificate")
    assert mismatch is False


def test_resolve_confident_different_known_type_is_mismatch():
    resolved, mismatch = resolve_form_type("income_certificate", "scholarship_application")
    assert resolved == "income_certificate"
    assert mismatch is True


def test_resolve_unknown_classification_defers_to_declared():
    resolved, mismatch = resolve_form_type("income_certificate", "unknown")
    assert resolved == "income_certificate"
    assert mismatch is False
