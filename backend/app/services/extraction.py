"""LLM-based structured field extraction, grounded in a deterministic recheck of each
value against its own source snippet and format validators — never the vision-LLM's
self-reported confidence alone (PRD §10; SPEC-PHASE1.md Decision 5).

Confidence rubric (SPEC-PHASE1.md §6.3), in priority order:
    format-invalid typed field            -> <= 0.40  (band low)
    value not found in its source snippet -> <= 0.55  (band low)
    a normalization/reformat was applied   -> cap 0.85 (band medium) — a date-format
                                               conversion is exactly the kind of mistake
                                               Phase 3's document_verification_tool exists
                                               to catch; Phase 1 prices that risk in now.
    typed field, snippet-contains, valid   -> 0.95-0.97 (band high)
    free-text, snippet-contains            -> 0.80-0.88, up to high if self-report is
                                               also high (self-report is only a tiebreaker
                                               within a band, never the sole signal)
    field absent                           -> no row written; reported as missing
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field

from app.config import settings
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
from app.services.ocr.vision_llm import DOC_TYPE_SCHEMAS, RawField
from app.services.ocr.vision_llm import extract as vision_extract

# Fields with a hard, deterministic format check. Anything else is free text (name/
# address) and is grounded on snippet-contains alone.
_TYPED_FIELDS = {"aadhaar_number", "pan_number", "dob", "gender"}
# Always require user confirmation regardless of confidence (SPEC-PHASE1.md Decision 2).
HIGH_STAKES_FIELDS = {"aadhaar_number", "pan_number", "dob"}

_VALIDATORS = {
    "aadhaar_number": is_valid_aadhaar,
    "pan_number": is_valid_pan,
    "dob": lambda v: parse_dob(v) is not None,
    "gender": lambda v: normalize_gender(v) is not None,
}
_NORMALIZERS = {
    "aadhaar_number": normalize_aadhaar,
    "pan_number": normalize_pan,
    "dob": normalize_dob,
    "gender": normalize_gender,
}


@dataclass
class GroundedField:
    field_name: str
    value: str  # canonical/normalized value — what gets encrypted and stored
    source_snippet: str
    confidence: float
    confidence_band: str  # high | medium | low
    high_stakes: bool
    format_valid: bool
    validators: dict


@dataclass
class ExtractionResult:
    type_mismatch: bool
    detected_doc_type: str | None
    fields: list[GroundedField] = dc_field(default_factory=list)
    missing_fields: list[str] = dc_field(default_factory=list)


def extract_profile_fields(images: list[bytes], declared_doc_type: str) -> ExtractionResult:
    raw = vision_extract(images, declared_doc_type)

    # Decision 6: user declares the type, the model also classifies; disagreement is
    # flagged rather than silently extracted against the wrong schema.
    if raw.detected_doc_type and raw.detected_doc_type != declared_doc_type:
        return ExtractionResult(type_mismatch=True, detected_doc_type=raw.detected_doc_type)

    schema_fields = DOC_TYPE_SCHEMAS[declared_doc_type]
    grounded: list[GroundedField] = []
    missing: list[str] = []

    for field_name in schema_fields:
        raw_field = raw.fields.get(field_name)
        if raw_field is None or not raw_field.present or not raw_field.value:
            missing.append(field_name)
            continue
        grounded.append(_ground_field(field_name, raw_field))

    return ExtractionResult(
        type_mismatch=False,
        detected_doc_type=raw.detected_doc_type,
        fields=grounded,
        missing_fields=missing,
    )


def _ground_field(field_name: str, raw: RawField) -> GroundedField:
    # Callers only invoke this once extract_profile_fields has already confirmed
    # raw.present and raw.value are truthy.
    assert raw.value is not None
    value = raw.value.strip()
    snippet = raw.source_snippet or ""
    contains = snippet_contains(value, snippet)

    is_typed = field_name in _TYPED_FIELDS
    format_valid = True
    final_value = value
    was_normalized = False

    if is_typed:
        format_valid = _VALIDATORS[field_name](value)
        if format_valid:
            normalized = _NORMALIZERS[field_name](value)
            if normalized is not None:
                was_normalized = normalized != value
                final_value = normalized

    confidence = _score(
        contains=contains,
        is_typed=is_typed,
        format_valid=format_valid,
        was_normalized=was_normalized,
        self_confidence=raw.self_confidence,
    )

    return GroundedField(
        field_name=field_name,
        value=final_value,
        source_snippet=snippet,
        confidence=confidence,
        confidence_band=_band(confidence),
        high_stakes=field_name in HIGH_STAKES_FIELDS,
        format_valid=format_valid,
        validators={
            "snippet_contains": contains,
            "format_valid": format_valid,
            "normalized": was_normalized,
        },
    )


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _score(
    *,
    contains: bool,
    is_typed: bool,
    format_valid: bool,
    was_normalized: bool,
    self_confidence: float,
) -> float:
    self_conf = _clamp01(self_confidence)

    if is_typed and not format_valid:
        return round(min(0.40, 0.20 + 0.20 * self_conf), 4)

    if not contains:
        return round(min(0.55, 0.30 + 0.25 * self_conf), 4)

    if was_normalized:
        return round(min(0.85, 0.75 + 0.10 * self_conf), 4)

    if is_typed:
        return round(0.95 + 0.02 * self_conf, 4)

    # Free-text field (name/address): snippet-contains, no normalization applies.
    if self_conf >= 0.9:
        return round(min(0.94, 0.88 + 0.06 * self_conf), 4)
    return round(0.80 + 0.08 * self_conf, 4)


def _band(confidence: float) -> str:
    if confidence >= settings.ocr_confidence_high:
        return "high"
    if confidence >= settings.ocr_confidence_medium:
        return "medium"
    return "low"
