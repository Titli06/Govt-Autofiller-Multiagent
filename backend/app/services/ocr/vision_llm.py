"""Primary OCR/vision extraction via a vision-LLM (Google Gemini).

Robust to handwriting, skew, rotation, and mixed Hindi/English text — used for all
messy real-world ID scans (the Tesseract path stays a stub in Phase 1; it's a later
cost optimization, not a correctness requirement).

The model is constrained via a strict JSON response schema (SPEC-PHASE1.md §3.2/§6.3)
to: classify the document itself, extract exactly the requested fields, and return each
field's verbatim source snippet. It must never invent a value for an absent field.
This module returns the *raw*, ungrounded extraction only — services/extraction.py is
responsible for grounding confidence in that snippet + format validation, never trusting
self_confidence alone (PRD §10).

Deviation note: PRD §5.1 named Claude/GPT-4V as the vision-LLM; this was swapped to
Gemini per an explicit later decision (see memory/phase1-decisions.md) while keeping this
module's public interface (DOC_TYPE_SCHEMAS, RawField, RawExtraction,
VisionExtractionError, extract()) unchanged, so extraction.py/tasks.py needed no changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
from google import genai
from google.genai import errors, types

from app.config import settings

# Per-doc-type target schema — the model is constrained to exactly these fields; anything
# else is rejected by the response schema, and absent fields come back present=False
# rather than invented.
DOC_TYPE_SCHEMAS: dict[str, list[str]] = {
    "aadhaar": ["full_name", "dob", "gender", "aadhaar_number", "address"],
    "pan": ["full_name", "father_name", "dob", "pan_number"],
}

_MEDIA_TYPE = "image/jpeg"  # services/preprocessing.py always normalizes to JPEG
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}


@dataclass
class RawField:
    value: str | None
    source_snippet: str | None
    self_confidence: float
    present: bool


@dataclass
class RawExtraction:
    detected_doc_type: str | None
    fields: dict[str, RawField] = field(default_factory=dict)


class VisionExtractionError(Exception):
    """Raised when the vision-LLM call fails or returns an unparsable response.

    `transient` distinguishes retryable failures (rate limit, server error, timeout,
    connection error) from terminal ones (bad request, auth, unparsable response) so the
    caller (ocr_extract_task) knows whether to retry or fail the document outright.
    """

    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


def _client() -> genai.Client:
    if not settings.gemini_api_key:
        raise VisionExtractionError("GEMINI_API_KEY is not configured", transient=False)
    return genai.Client(api_key=settings.gemini_api_key)


def _response_schema(field_names: list[str]) -> dict:
    field_schema = {
        "type": "object",
        "properties": {
            "present": {"type": "boolean"},
            "value": _NULLABLE_STRING,
            "source_snippet": {
                **_NULLABLE_STRING,
                "description": (
                    "Verbatim text copied from the document that this value was read "
                    "from. Must literally contain the value."
                ),
            },
            "self_confidence": {"type": "number"},
        },
        "required": ["present", "value", "source_snippet", "self_confidence"],
    }
    return {
        "type": "object",
        "properties": {
            "detected_doc_type": {
                "type": "string",
                "enum": list(DOC_TYPE_SCHEMAS.keys()),
            },
            "fields": {
                "type": "object",
                "properties": {name: field_schema for name in field_names},
                "required": field_names,
            },
        },
        "required": ["detected_doc_type", "fields"],
    }


def _build_contents(images: list[bytes], declared_doc_type: str) -> list:
    parts: list = [
        types.Part.from_bytes(data=img, mime_type=_MEDIA_TYPE) for img in images
    ]
    parts.append(
        f"This document was uploaded as a declared type of '{declared_doc_type}'. "
        "Classify the document type yourself (don't just assume the declared type is "
        "correct) and extract exactly the requested fields as JSON matching the response "
        "schema. For every field you report as present, source_snippet must be the "
        "verbatim text on the document that the value was read from. Never invent a "
        "value — if a field genuinely isn't on the document, set present=false and leave "
        "value and source_snippet null."
    )
    return parts


def extract(images: list[bytes], declared_doc_type: str) -> RawExtraction:
    field_names = DOC_TYPE_SCHEMAS.get(declared_doc_type)
    if field_names is None:
        raise VisionExtractionError(
            f"unknown declared_doc_type: {declared_doc_type}", transient=False
        )

    client = _client()
    try:
        response = client.models.generate_content(
            model=settings.vision_model,
            contents=_build_contents(images, declared_doc_type),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=_response_schema(field_names),
            ),
        )
    except errors.ServerError as exc:
        raise VisionExtractionError(str(exc), transient=True) from exc
    except errors.ClientError as exc:
        # 429 (rate limit) is the one 4xx worth retrying; the rest (400/401/403/404) won't
        # resolve on their own.
        raise VisionExtractionError(str(exc), transient=exc.code == 429) from exc
    except errors.APIError as exc:
        raise VisionExtractionError(str(exc), transient=False) from exc
    except httpx.TransportError as exc:
        # Network-level failures (connection refused, DNS, timeout) aren't wrapped by
        # the SDK's own error hierarchy.
        raise VisionExtractionError(str(exc), transient=True) from exc

    text = response.text
    if not text:
        raise VisionExtractionError("model returned an empty response", transient=False)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VisionExtractionError(f"model returned invalid JSON: {exc}", transient=False) from exc

    fields = {
        name: RawField(
            value=raw.get("value"),
            source_snippet=raw.get("source_snippet"),
            self_confidence=float(raw.get("self_confidence") or 0.0),
            present=bool(raw.get("present", False)),
        )
        for name, raw in (payload.get("fields") or {}).items()
    }
    return RawExtraction(detected_doc_type=payload.get("detected_doc_type"), fields=fields)
