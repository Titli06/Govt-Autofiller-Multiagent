"""field_mapping_tool.infer_schema: semantic label->canonical-key mapping (Phase 4's
net-new differentiator, SPEC-PHASE4.md §3). Pure — a stub label_mapper, no LLM/DB."""

from __future__ import annotations

from app.agent.tools import field_mapping_tool as fmt
from app.config import settings
from app.services.form_placement.document_ai import DetectedField


def _detected(name="Father's Name", page=1, value_bbox=(0.1, 0.2, 0.4, 0.25), confidence=0.9):
    return DetectedField(name=name, page=page, value_bbox=value_bbox, confidence=confidence)


def _mapper(mapping: dict) -> fmt.LabelMapper:
    return lambda labels, canonical_keys: mapping


# --- tier -> cap / profile_key / high_stakes -------------------------------------------


def test_exact_tier_maps_profile_key_and_caps_confidence():
    detected = [_detected("Father's Name")]
    mapper = _mapper({"Father's Name": {"profile_key": "father_name", "tier": "exact"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.profile_key == "father_name"
    assert spec.mapping_tier == "exact"
    assert spec.mapping_cap == settings.map_cap_exact
    assert spec.high_stakes is False


def test_strong_tier_caps_confidence():
    detected = [_detected("Applicant Name")]
    mapper = _mapper({"Applicant Name": {"profile_key": "full_name", "tier": "strong"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.mapping_cap == settings.map_cap_strong


def test_weak_tier_caps_confidence():
    detected = [_detected("Guardian")]
    mapper = _mapper({"Guardian": {"profile_key": "father_name", "tier": "weak"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.mapping_cap == settings.map_cap_weak


def test_none_tier_is_no_mapping_with_no_cap():
    detected = [_detected("Purpose of Application")]
    mapper = _mapper({"Purpose of Application": {"profile_key": None, "tier": "none"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.profile_key is None
    assert spec.mapping_tier is None
    assert spec.mapping_cap is None
    assert spec.high_stakes is False


def test_label_missing_from_mapper_response_treated_as_no_mapping():
    detected = [_detected("Weird Label")]
    mapper = _mapper({})  # mapper didn't return anything for this label

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.profile_key is None
    assert spec.mapping_cap is None


def test_mapper_returning_unknown_key_treated_as_no_mapping():
    detected = [_detected("Some Label")]
    mapper = _mapper({"Some Label": {"profile_key": "not_a_real_key", "tier": "exact"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.profile_key is None
    assert spec.mapping_cap is None


def test_high_stakes_only_for_dob_aadhaar_pan():
    detected = [_detected("DOB"), _detected("Aadhaar No", page=1), _detected("PAN No"), _detected("Name")]
    mapper = _mapper(
        {
            "DOB": {"profile_key": "dob", "tier": "exact"},
            "Aadhaar No": {"profile_key": "aadhaar_number", "tier": "exact"},
            "PAN No": {"profile_key": "pan_number", "tier": "exact"},
            "Name": {"profile_key": "full_name", "tier": "exact"},
        }
    )

    specs = fmt.infer_schema(detected, mapper)
    by_key = {s.profile_key: s for s in specs}
    assert by_key["dob"].high_stakes is True
    assert by_key["aadhaar_number"].high_stakes is True
    assert by_key["pan_number"].high_stakes is True
    assert by_key["full_name"].high_stakes is False


# --- placement (bbox) ------------------------------------------------------------------


def test_low_confidence_box_yields_no_placement(monkeypatch):
    monkeypatch.setattr(settings, "documentai_min_confidence", 0.5)
    detected = [_detected("Name", confidence=0.2)]
    mapper = _mapper({"Name": {"profile_key": "full_name", "tier": "exact"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.placement is None


def test_none_bbox_yields_no_placement():
    detected = [_detected("Name", value_bbox=None, confidence=0.9)]
    mapper = _mapper({"Name": {"profile_key": "full_name", "tier": "exact"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.placement is None


def test_confident_box_yields_normalized_placement():
    detected = [_detected("Name", page=2, value_bbox=(0.1, 0.2, 0.3, 0.25), confidence=0.9)]
    mapper = _mapper({"Name": {"profile_key": "full_name", "tier": "exact"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.placement == {"page": 2, "bbox": [0.1, 0.2, 0.3, 0.25]}


def test_no_mapping_field_can_still_get_placement():
    """An unmapped field is still placeable — Decision 9: it becomes a blank,
    always-outstanding field the user hand-fills in review, at its detected box."""
    detected = [_detected("Purpose", value_bbox=(0.1, 0.2, 0.3, 0.25), confidence=0.9)]
    mapper = _mapper({"Purpose": {"profile_key": None, "tier": "none"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.profile_key is None
    assert spec.placement == {"page": 1, "bbox": [0.1, 0.2, 0.3, 0.25]}


# --- field_name slug + dedup -------------------------------------------------------------


def test_slug_normalizes_label_to_field_name():
    detected = [_detected("Father's Name")]
    mapper = _mapper({"Father's Name": {"profile_key": "father_name", "tier": "exact"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.name == "father_s_name"


def test_duplicate_labels_deduped_with_numeric_suffix():
    detected = [_detected("Name"), _detected("Name")]
    mapper = _mapper({"Name": {"profile_key": "full_name", "tier": "exact"}})

    specs = fmt.infer_schema(detected, mapper)
    assert [s.name for s in specs] == ["name", "name_2"]


def test_two_labels_mapping_to_same_canonical_key_both_kept():
    detected = [_detected("Applicant Name", page=1), _detected("Declarant Name", page=1)]
    mapper = _mapper(
        {
            "Applicant Name": {"profile_key": "full_name", "tier": "exact"},
            "Declarant Name": {"profile_key": "full_name", "tier": "strong"},
        }
    )

    specs = fmt.infer_schema(detected, mapper)
    assert len(specs) == 2
    assert all(s.profile_key == "full_name" for s in specs)
    assert {s.name for s in specs} == {"applicant_name", "declarant_name"}


def test_format_is_always_as_is():
    detected = [_detected("DOB")]
    mapper = _mapper({"DOB": {"profile_key": "dob", "tier": "exact"}})

    [spec] = fmt.infer_schema(detected, mapper)
    assert spec.format == "as_is"
