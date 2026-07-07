"""Field-detection fallback for template-less forms — Google Document AI Form Parser.

Purpose-built form key-value + bounding-box detection, used as the placement source
for a form with NO known template (SPEC-PHASE3.md Decision 6, §8.4.3) — far more
reliable than asking a vision-LLM for pixel coordinates, which is why this project
deliberately does not do that (see services/ocr/vision_llm.py: no `locate_fields`).

**Phase 3 defines the interface + config only.** This path is unreachable in Phase 3:
`POST /forms/upload` `422`s any `form_type` not already in the template registry
(app/templates/), so every Phase-3 form already has a placement template and never
reaches here. Schema inference for unseen forms (the only way to reach a template-less
fill) is Phase 4, which is also where the real Document AI call, response caching, and
routing of low-confidence/undetected fields to the renderer's "unplaced fields" page
(services/form_renderer.py) get wired up.

Auth note: Document AI uses GCP service-account auth
(`GOOGLE_APPLICATION_CREDENTIALS`), not the API-key auth `services/ocr/vision_llm.py`
uses for Gemini — provisioning that is a Phase-4 task, not done here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass
class DetectedField:
    name: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1), page points
    confidence: float


class DocumentAIError(Exception):
    """Raised by detect_fields in every Phase 3 call — the real integration is Phase 4."""


def detect_fields(images: list[bytes]) -> list[DetectedField]:
    _ = (settings.documentai_location, settings.documentai_processor_id)  # Phase-4 wiring
    raise DocumentAIError(
        "Document AI field detection is not implemented in Phase 3 — schema inference "
        "for template-less forms (and this fallback) ships in Phase 4."
    )
