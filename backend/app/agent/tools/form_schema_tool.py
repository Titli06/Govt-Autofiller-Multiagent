"""form_schema_tool — identify the form type and its required fields.

Uses a known template from app/templates/ when the form is recognized. When it is
not, infers the field schema from the uploaded form itself (UC3/FR4 — the hardest,
most differentiating path). Inferred schemas default to lower confidence downstream.

Phase 2 implements only the known-template branch (registry + mismatch decision);
schema inference is Phase 4.
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


@dataclass
class Template:
    form_type: str
    display_name: str
    required_fields: list[TemplateField] = field(default_factory=list)


def _validate_format(fmt: str, template_name: str) -> None:
    if fmt in _LITERAL_FORMATS or fmt.startswith("date:"):
        return
    raise TemplateError(f"template {template_name}: unsupported format grammar {fmt!r}")


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

    fields: list[TemplateField] = []
    for raw in raw_fields:
        profile_key = raw.get("profile_key")
        if profile_key is not None and profile_key not in CANONICAL_PROFILE_KEYS:
            raise TemplateError(f"template {path.name}: unknown profile_key {profile_key!r}")
        fmt = raw.get("format", "as_is")
        _validate_format(fmt, path.name)
        fields.append(
            TemplateField(
                name=raw["name"],
                profile_key=profile_key,
                high_stakes=bool(raw.get("high_stakes", False)),
                format=fmt,
            )
        )

    return Template(
        form_type=form_type,
        display_name=data.get("display_name", form_type),
        required_fields=fields,
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
