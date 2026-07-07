"""Field-detection fallback for template-less forms — Google Document AI Form Parser.

Purpose-built form key-value + bounding-box detection, used as the placement source
for a form with NO known template (SPEC-PHASE4.md Decision 6, §6.4) — far more
reliable than asking a vision-LLM for pixel coordinates, which is why this project
deliberately does not do that (see services/ocr/vision_llm.py: no `locate_fields`).

Auth: Document AI uses GCP service-account auth (`GOOGLE_APPLICATION_CREDENTIALS`),
not the API-key auth `services/ocr/vision_llm.py` uses for Gemini via `google-genai` —
"same GCP project" means shared billing only, not a drop-in.

The `google.cloud.documentai_v1`/`google.api_core` imports are deferred into the
functions that need them (rather than at module scope) so this module stays
import-safe when the dependency/credentials aren't configured — unrelated tests and
app startup never require GCP credentials just because this file is imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings


@dataclass
class DetectedField:
    name: str  # the detected field LABEL text (the mapping key, field_mapping_tool)
    page: int  # 1-based page index
    value_bbox: tuple[float, float, float, float] | None  # normalized (x0, y0, x1, y1), or None
    confidence: float  # the detected VALUE region's confidence


class DocumentAIError(Exception):
    """Mirrors VisionExtractionError's transient/terminal split (services/ocr/vision_llm.py)
    so fill_form_task can retry or fail the same way for either provider."""

    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


def _client() -> Any:
    if not settings.documentai_processor_id:
        raise DocumentAIError("Document AI processor is not configured", transient=False)
    from google.cloud import documentai_v1 as documentai

    return documentai.DocumentProcessorServiceClient(
        client_options={
            "api_endpoint": f"{settings.documentai_location}-documentai.googleapis.com"
        }
    )


def _processor_name(client: Any) -> str:
    return client.processor_path(
        settings.documentai_project_id, settings.documentai_location, settings.documentai_processor_id
    )


def _process_page(client: Any, name: str, image_bytes: bytes) -> Any:
    """One Form Parser call for one page image; returns the response `Document`.
    Transient (retryable) vs terminal error classification mirrors vision_llm's
    ServerError/ClientError(429)/APIError split."""
    from google.api_core import exceptions as gax_exceptions
    from google.cloud import documentai_v1 as documentai

    request = documentai.ProcessRequest(
        name=name,
        raw_document=documentai.RawDocument(content=image_bytes, mime_type="image/jpeg"),
    )
    try:
        result = client.process_document(request=request)
    except (gax_exceptions.ServiceUnavailable, gax_exceptions.DeadlineExceeded, gax_exceptions.ResourceExhausted) as exc:
        raise DocumentAIError(str(exc), transient=True) from exc
    except gax_exceptions.GoogleAPICallError as exc:
        raise DocumentAIError(str(exc), transient=False) from exc
    return result.document


def _layout_text(full_text: str, layout: Any) -> str:
    """Reconstructs a Layout's text from the document's full text + its TextAnchor
    segments — Document AI stores field text as offsets into `document.text`, not
    inline. Never logs `full_text`/the reconstructed value (CLAUDE.md — no raw PII in
    logs); this is a pure string op, callers are responsible for not logging output."""
    anchor = getattr(layout, "text_anchor", None)
    segments = getattr(anchor, "text_segments", None) or []
    if not segments:
        return ""
    parts = []
    for seg in segments:
        start = int(seg.start_index) if seg.start_index else 0
        end = int(seg.end_index)
        parts.append(full_text[start:end])
    return "".join(parts).strip()


def _normalized_bbox(layout: Any) -> tuple[float, float, float, float] | None:
    """Converts a Layout's normalized bounding-poly vertices into a normalized
    (x0, y0, x1, y1) box — page-fraction coordinates, DPI/page-size independent by
    construction (SPEC-PHASE4.md default implementation choices)."""
    poly = getattr(layout, "bounding_poly", None)
    vertices = getattr(poly, "normalized_vertices", None) or []
    if not vertices:
        return None
    xs = [v.x for v in vertices]
    ys = [v.y for v in vertices]
    return (min(xs), min(ys), max(xs), max(ys))


def _fields_from_document(document: Any, page_index: int) -> list[DetectedField]:
    """Pure conversion from a Document AI `Document` response (or a duck-typed fake
    with the same page.form_fields[].field_name/field_value.text_anchor/bounding_poly
    shape) into DetectedFields — kept separate from the network call so it's
    unit-testable without mocking the GCP client (SPEC-PHASE4.md §6.4). A field with
    no label text is skipped; a field with no value layout gets value_bbox=None
    (routes to the renderer's "unplaced" page, not a coordinate guess)."""
    fields: list[DetectedField] = []
    for page in document.pages:
        for form_field in page.form_fields:
            label = _layout_text(document.text, form_field.field_name)
            if not label:
                continue
            value_layout = getattr(form_field, "field_value", None)
            value_bbox = _normalized_bbox(value_layout) if value_layout is not None else None
            confidence = float(getattr(value_layout, "confidence", 0.0) or 0.0) if value_layout is not None else 0.0
            fields.append(DetectedField(name=label, page=page_index, value_bbox=value_bbox, confidence=confidence))
    return fields


def detect_fields(images: list[bytes]) -> list[DetectedField]:
    """Detects form fields (label + value bounding box + confidence) on each page via
    Google Document AI Form Parser — purpose-built for form key-value + bounding-box
    detection, far more reliable than a vision-LLM at pixel coordinates. Raises
    DocumentAIError (transient=True) on a retryable infra error, or terminal on bad
    config/auth/request. Never logs image bytes, extracted labels, or values."""
    client = _client()
    name = _processor_name(client)

    detected: list[DetectedField] = []
    for page_index, image_bytes in enumerate(images, start=1):
        document = _process_page(client, name, image_bytes)
        detected.extend(_fields_from_document(document, page_index))
    return detected
