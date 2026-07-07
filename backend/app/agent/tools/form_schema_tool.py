"""form_schema_tool — identify the form type and its required fields.

Uses a known template from app/templates/ when the form is recognized. When it is
not, infers the field schema from the uploaded form itself (UC3/FR4 — the hardest,
most differentiating path). Inferred schemas default to lower confidence downstream.

This module implements only the known-template branch (registry, mismatch decision,
`HIGH_STAKES_PROFILE_KEYS`); the inference branch itself lives in
agent/graph.py (`_form_schema_node`) + agent/tools/field_mapping_tool.py
(SPEC-PHASE4.md §6.2/§3) — this module supplies the shared `TemplateField` shape and
canonical vocabulary both branches synthesize/consume.

Phase 3 (SPEC-PHASE3.md §8.4.1) extends each template with a `placement` block: a
template-level `reference_page_size`/`default_font_size` and, per field, either a
named `acro_field` (fillable-PDF widget) or absolute `(x, y[, font_size])` coordinates
authored against that reference page size. Placement is validated here — at registry
load time — so a malformed template fails app startup loudly instead of mid-render
(services/form_renderer.py). Placement is not a fill-graph concern; it is consumed
only by the renderer at download time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Canonical profile vocabulary a template's profile_key must draw from (SPEC-PHASE1.md
# §3.1) — a typo here should fail template loading loudly, not silently no_candidate
# every field at fill time.
CANONICAL_PROFILE_KEYS = {
    "full_name",
    "father_name",
    "dob",
    "gender",
    "address",
    "aadhaar_number",
    "pan_number",
}

# Canonical keys that are always high-stakes regardless of source (Phase 4 §6.3) —
# templates declare high_stakes by hand per field; an inferred field has no such
# declaration, so it's derived from the matched canonical key instead.
HIGH_STAKES_PROFILE_KEYS = {"dob", "aadhaar_number", "pan_number"}

# Format grammar supported by profile_lookup_tool.apply_format (§3.3). "date:<strftime>"
# is checked separately since the strftime suffix is open-ended.
_LITERAL_FORMATS = {"as_is", "upper", "single_line"}

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


class TemplateError(Exception):
    """A template file is malformed, or an unknown form_type was requested. Registry
    validation happens once at startup so a typo fails fast rather than mid-fill."""


@dataclass
class TemplateField:
    name: str
    profile_key: str | None
    high_stakes: bool
    format: str = "as_is"
    # {"acro_field": str} OR {"page": int, "x": float, "y": float, "font_size"?: float}
    # (template fields) OR {"page": int, "bbox": [x0,y0,x1,y1]} normalized 0-1
    # (inferred fields, Phase 4); None => no known placement, the renderer routes this
    # field to the "unplaced" page.
    placement: dict | None = None
    # Phase 4 — set only for a field synthesized by field_mapping_tool.infer_schema;
    # None for every template field (loaded from JSON, never has these).
    mapping_tier: str | None = None  # "exact" | "strong" | "weak" | None (no cap to apply)
    mapping_cap: float | None = None  # confidence_scorer_tool caps the score to this


@dataclass
class Template:
    form_type: str
    display_name: str
    required_fields: list[TemplateField] = field(default_factory=list)
    # {"reference_page_size": [width, height], "default_font_size": float}; the page
    # size per-field coordinates were authored against (§8.4.1). Empty dict if absent.
    placement: dict = field(default_factory=dict)


def _validate_format(fmt: str, template_name: str) -> None:
    if fmt in _LITERAL_FORMATS or fmt.startswith("date:"):
        return
    raise TemplateError(f"template {template_name}: unsupported format grammar {fmt!r}")


_PLACEMENT_COORD_KEYS = {"page", "x", "y", "font_size"}


def _validate_field_placement(placement: object, template_name: str, field_name: str) -> None:
    """Validates one field's placement shape (§8.4.1): a non-empty `acro_field`, or
    numeric `page`/`x`/`y` (+ optional `font_size`) with page >= 1. None is allowed —
    it just means this field has no known placement yet."""
    if placement is None:
        return
    if not isinstance(placement, dict):
        raise TemplateError(f"template {template_name}: field {field_name!r} placement must be an object")

    if "acro_field" in placement:
        extra = set(placement) - {"acro_field"}
        if extra:
            raise TemplateError(
                f"template {template_name}: field {field_name!r} placement mixes acro_field with {extra}"
            )
        acro_field = placement["acro_field"]
        if not isinstance(acro_field, str) or not acro_field.strip():
            raise TemplateError(
                f"template {template_name}: field {field_name!r} acro_field must be a non-empty string"
            )
        return

    unknown = set(placement) - _PLACEMENT_COORD_KEYS
    if unknown:
        raise TemplateError(f"template {template_name}: field {field_name!r} placement has unknown keys {unknown}")
    if "page" not in placement or "x" not in placement or "y" not in placement:
        raise TemplateError(f"template {template_name}: field {field_name!r} placement needs page, x and y")
    page = placement["page"]
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise TemplateError(f"template {template_name}: field {field_name!r} placement.page must be an int >= 1")
    for key in ("x", "y", "font_size"):
        if key in placement and (not isinstance(placement[key], (int, float)) or isinstance(placement[key], bool)):
            raise TemplateError(f"template {template_name}: field {field_name!r} placement.{key} must be numeric")


def _validate_template_placement(placement: object, template_name: str) -> dict:
    """Validates the template-level placement block (reference page size + default
    font size). Returns {} if the template declares no placement block at all
    (an older/placement-less template is still loadable; every field then lands on
    the renderer's "unplaced" page)."""
    if placement is None:
        return {}
    if not isinstance(placement, dict):
        raise TemplateError(f"template {template_name}: placement must be an object")

    ref_size = placement.get("reference_page_size")
    if ref_size is not None:
        valid = (
            isinstance(ref_size, list)
            and len(ref_size) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in ref_size)
        )
        if not valid:
            raise TemplateError(
                f"template {template_name}: placement.reference_page_size must be [width, height]"
            )

    default_font_size = placement.get("default_font_size")
    if default_font_size is not None and (
        not isinstance(default_font_size, (int, float)) or isinstance(default_font_size, bool)
    ):
        raise TemplateError(f"template {template_name}: placement.default_font_size must be numeric")

    return placement


def _load_template_file(path: Path) -> Template:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"template {path.name}: invalid JSON ({exc})") from exc

    form_type = data.get("form_type")
    if form_type != path.stem:
        raise TemplateError(f"template {path.name}: form_type must match filename stem")

    raw_fields = data.get("required_fields") or []
    if not raw_fields:
        raise TemplateError(f"template {path.name}: required_fields must be non-empty")

    template_placement = _validate_template_placement(data.get("placement"), path.name)

    fields: list[TemplateField] = []
    for raw in raw_fields:
        profile_key = raw.get("profile_key")
        if profile_key is not None and profile_key not in CANONICAL_PROFILE_KEYS:
            raise TemplateError(f"template {path.name}: unknown profile_key {profile_key!r}")
        fmt = raw.get("format", "as_is")
        _validate_format(fmt, path.name)
        placement = raw.get("placement")
        _validate_field_placement(placement, path.name, raw["name"])
        fields.append(
            TemplateField(
                name=raw["name"],
                profile_key=profile_key,
                high_stakes=bool(raw.get("high_stakes", False)),
                format=fmt,
                placement=placement,
            )
        )

    return Template(
        form_type=form_type,
        display_name=data.get("display_name", form_type),
        required_fields=fields,
        placement=template_placement,
    )


def load_registry(templates_dir: Path = _TEMPLATES_DIR) -> dict[str, Template]:
    """Reads and validates every template in templates_dir. No caching — the registry
    is two small JSON files; re-reading is cheap and keeps tests (which point at a
    temp dir) free of stale-cache surprises."""
    registry: dict[str, Template] = {}
    for path in sorted(templates_dir.glob("*.json")):
        template = _load_template_file(path)
        registry[template.form_type] = template
    if not registry:
        raise TemplateError(f"no templates found in {templates_dir}")
    return registry


def known_types(templates_dir: Path = _TEMPLATES_DIR) -> list[str]:
    return sorted(load_registry(templates_dir).keys())


def load_template(form_type: str, templates_dir: Path = _TEMPLATES_DIR) -> Template:
    registry = load_registry(templates_dir)
    template = registry.get(form_type)
    if template is None:
        raise TemplateError(f"unknown form_type: {form_type}")
    return template


def resolve_form_type(
    declared_form_type: str, detected_form_type: str | None
) -> tuple[str, bool]:
    """Returns (resolved_form_type, type_mismatch).

    Decision 1: only a *confident* detection of a *different known* type blocks the
    fill; an 'unknown'/uncertain classification defers to the user's declared type
    rather than blocking a legitimate fill the classifier merely struggled with.
    """
    known = set(known_types())
    mismatch = bool(
        detected_form_type
        and detected_form_type in known
        and detected_form_type != declared_form_type
    )
    return declared_form_type, mismatch
