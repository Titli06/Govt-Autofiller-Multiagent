"""field_mapping_tool — semantic field-to-profile mapping for an INFERRED schema.

The PRD's stated differentiator: *semantic* field matching over unstructured
paperwork with no fixed schema, not string matching (SPEC-PHASE4.md §3). Deliberately
NOT an extension of agent/tools/profile_lookup_tool.py — that tool only does exact,
human-pre-declared profile_key lookup from a template JSON, with zero semantic
capability. An inferred form has no human-authored mapping, so this tool turns each
Document-AI-detected field label into a synthesized TemplateField the rest of the
pipeline (profile_lookup -> document_verification -> confidence_scorer) consumes
identically to a template field.

Pure over its inputs plus an injected `label_mapper` callable — no DB access, no real
LLM call, so it's testable with a stub (mirrors agent/tools/document_verification_tool.py).
"""

from __future__ import annotations

import re
from typing import Callable

from app.agent.tools.form_schema_tool import (
    CANONICAL_PROFILE_KEYS,
    HIGH_STAKES_PROFILE_KEYS,
    TemplateField,
)
from app.config import settings
from app.services.form_placement.document_ai import DetectedField

# labels, canonical_keys -> {label: {"profile_key": str | None, "tier": "exact"|"strong"|"weak"|"none"}}
LabelMapper = Callable[[list[str], list[str]], dict[str, dict]]

_TIER_CAPS = {"exact": "map_cap_exact", "strong": "map_cap_strong", "weak": "map_cap_weak"}


def _slug(label: str) -> str:
    """Stable, DB-safe field_name from a detected label ('Father's Name' -> 'fathers_name')."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return (slug or "field")[:64]


def _tier_cap(tier: str) -> float | None:
    attr = _TIER_CAPS.get(tier)
    return getattr(settings, attr) if attr else None


def infer_schema(detected: list[DetectedField], label_mapper: LabelMapper) -> list[TemplateField]:
    """Synthesizes TemplateField specs from Document-AI-detected fields, via one
    batched semantic label->canonical-key mapping call (SPEC-PHASE4.md §3.3)."""
    labels = [d.name for d in detected]
    mapping = label_mapper(labels, sorted(CANONICAL_PROFILE_KEYS))

    specs: list[TemplateField] = []
    used: dict[str, int] = {}
    for d in detected:
        m = mapping.get(d.name, {})
        key = m.get("profile_key")
        tier = m.get("tier", "none")
        if key not in CANONICAL_PROFILE_KEYS:
            key, tier = None, "none"

        placement = None
        if d.value_bbox is not None and d.confidence >= settings.documentai_min_confidence:
            placement = {"page": d.page, "bbox": list(d.value_bbox)}

        base = _slug(d.name)
        count = used.get(base, 0) + 1
        used[base] = count
        field_name = base if count == 1 else f"{base}_{count}"

        specs.append(
            TemplateField(
                name=field_name,
                profile_key=key,
                high_stakes=key in HIGH_STAKES_PROFILE_KEYS,
                format="as_is",
                placement=placement,
                mapping_tier=None if key is None else tier,
                mapping_cap=None if key is None else _tier_cap(tier),
            )
        )
    return specs
