"""Render the approved field values into a downloadable filled form (PDF).

Overlays each field's effective value onto the ORIGINAL uploaded blank form -> a
single PDF (SPEC-PHASE3.md §8.4, Decision 5/6). Placement is template-first and
deterministic — no AI/LLM call at render time:
    1. a native AcroForm widget (matched by name) when the uploaded PDF has one —
       PREFERRED: no pixel math, and it's skew-immune (the widget carries its own
       position on the page).
    2. else an absolute (x, y) coordinate declared in the form's template
       (app/templates/{form_type}.json `placement`), authored against a
       `reference_page_size` and scaled to the actual uploaded page.
    3. else the field is listed on an appended "Additional fields" page — nothing is
       ever silently dropped.
A field with no value (missing / approved-blank) is skipped entirely — this module
never invents data. Every page is stamped with the `render_watermark_text` watermark;
this output is downloaded by the authenticated owner and is NEVER submitted anywhere
(FR7).

*** Phase 4: inferred-form placement (SPEC-PHASE4.md §7) *** An INFERRED form
(schema_source="inferred") has no template file, so there is no per-form-type
placement JSON to load. Instead each RenderField carries its OWN normalized bbox
(`placement={"page": int, "bbox": [x0,y0,x1,y1]}`, 0-1 page fractions, from
FormField.placement — Document-AI-detected, field_mapping_tool.infer_schema) which
this module scales to the actual page rect at render time — DPI/page-size
independent by construction. No AcroForm attempt on this path (an unseen scanned
form has no named widgets to match). A field with no placement (undetected/
low-confidence box) lands on the same appended "Additional fields" page as a
template field with no coordinate/acro_field match — nothing is ever silently
dropped, on either path.

*** Font limitation (found live-testing, not yet fixed) *** `insert_text()` below uses
PyMuPDF's default base-14 font (Helvetica/WinAnsi), which only covers Latin-1. A
non-Latin-1 character silently renders as a substitute glyph instead of erroring —
confirmed live when an em-dash in `render_watermark_text`'s old default came out as
"·" on the actual downloaded PDF (fixed by using a plain hyphen instead, see
config.py). The same silent corruption would hit any field value containing
non-Latin-1 script — e.g. a name extracted verbatim in Devanagari from a source ID
(free-text fields are never transliterated, see services/extraction.py). Embedding a
Unicode-capable TTF (e.g. Noto Sans, bundled cross-platform for the Linux container)
would fix this properly; not done here — out of SPEC-PHASE3.md's scope, tracked as a
follow-up.

*** Coordinate-path limitation (read before touching the `x`/`y` branch) ***
Absolute-coordinate placement assumes a reasonably flat, upright scan whose page
matches the template's `reference_page_size` layout. A skewed, rotated, or heavily
cropped upload WILL misplace inserted text — these are static points with no
awareness of the page's actual rotation, and this renderer does not (and, without
OCR-level box detection, cannot) auto-correct skew. `services/image_quality.py` runs
a best-effort skew check at fill time and records a non-blocking
`Form.placement_warning` for a significantly rotated scan, surfaced in the review UI —
but the fill/render still completes. Prefer the AcroForm path (branch 1 above)
whenever the uploaded form is a fillable PDF with named widgets; it has no such
limitation. See README.md for the user-facing explanation of this tradeoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import fitz  # PyMuPDF

from app.agent.tools.form_schema_tool import load_template
from app.config import settings

_CONTENT_TYPE_FILETYPE = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}

_DEFAULT_FONT_SIZE = 10
_WATERMARK_FONT_SIZE = 9
_DEFAULT_PAGE_SIZE = (595.0, 842.0)  # A4 in points — used only if a template omits it


@dataclass
class RenderField:
    """One field's effective value, ready to place. `value=None` (missing or
    explicitly approved-blank) is skipped — we never invent data on the form."""

    field_name: str
    value: str | None
    # Phase 4: this field's OWN normalized placement, for an INFERRED form only
    # (None for a template form — the renderer looks placement up from the
    # template JSON there instead, keyed by field_name).
    placement: dict | None = None


class RenderError(Exception):
    """Raised when the blank form bytes can't be opened for rendering."""


def _open(blank_bytes: bytes, content_type: str) -> fitz.Document:
    filetype = _CONTENT_TYPE_FILETYPE.get(content_type, "pdf")
    try:
        doc = fitz.open(stream=blank_bytes, filetype=filetype)
        if not doc.is_pdf:
            # An image upload opens as a non-editable pseudo-document (no Shape/text
            # support) — convert it to a real one-page PDF so the rest of this module
            # can treat image and PDF uploads identically (§8.4.2).
            pdf_bytes = doc.convert_to_pdf()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return doc
    except Exception as exc:
        raise RenderError(f"could not open blank form for rendering: {type(exc).__name__}") from exc


def _widget_index(doc: fitz.Document) -> dict[str, tuple[Any, Any]]:
    """Maps AcroForm field name -> (page, widget), across every page. Empty for a
    plain (non-fillable) PDF or an image upload.

    Keeps the owning `page` object alongside the widget: PyMuPDF widgets need their
    parent Page kept alive to be updatable — if the Page were only a disposable loop
    variable here, it would be garbage-collected before the caller writes the value,
    and widget.update() would fail with "Annot is not bound to a page".
    """
    index: dict[str, tuple[Any, Any]] = {}
    for page in doc:
        for widget in page.widgets() or []:
            if widget.field_name:
                index[widget.field_name] = (page, widget)
    return index


def _scale(actual_rect: "fitz.Rect", ref_size: list[float] | tuple[float, float] | None) -> tuple[float, float]:
    if not ref_size:
        return 1.0, 1.0
    ref_w, ref_h = ref_size
    if not ref_w or not ref_h:
        return 1.0, 1.0
    return actual_rect.width / ref_w, actual_rect.height / ref_h


def _stamp_watermark(doc: fitz.Document) -> None:
    for page in doc:
        rect = page.rect
        page.insert_text(
            (max(rect.width / 2 - 90, 10), max(rect.height - 20, 10)),
            settings.render_watermark_text,
            fontsize=_WATERMARK_FONT_SIZE,
            color=(0.6, 0.6, 0.6),
        )


def _append_unplaced_page(doc: fitz.Document, unplaced: list[tuple[str, str]], ref_size) -> None:
    width, height = ref_size or _DEFAULT_PAGE_SIZE
    page = doc.new_page(width=width, height=height)
    page.insert_text((50, 50), "Additional fields", fontsize=14)
    y = 90
    for name, value in unplaced:
        page.insert_text((50, y), f"{name.replace('_', ' ')}: {value}", fontsize=10)
        y += 20


def _render_inferred(doc: fitz.Document, fields: list[RenderField], unplaced: list[tuple[str, str]]) -> None:
    """Phase 4 (SPEC-PHASE4.md §7): places each value at its OWN detected, normalized
    bbox — no template, no AcroForm attempt. A field with no placement (undetected/
    low-confidence box, or out-of-range page) lands on the appended page instead."""
    for f in fields:
        if f.value is None:
            continue  # missing / approved-blank — never invent data

        placed = False
        if f.placement is not None:
            page_index = f.placement["page"] - 1
            if 0 <= page_index < doc.page_count:
                page = doc[page_index]
                rect = page.rect
                x0, y0, x1, y1 = f.placement["bbox"]
                box_height = (y1 - y0) * rect.height
                font_size = max(6.0, min(box_height * 0.7, _DEFAULT_FONT_SIZE)) if box_height > 0 else _DEFAULT_FONT_SIZE
                # Normalized (0-1) coordinates scale to the ACTUAL page rect, so this
                # is DPI/page-size independent by construction — but still assumes
                # the detected box itself sits on a flat, upright scan (see the
                # skew-guard note in the module docstring).
                page.insert_text((x0 * rect.width + 2, y1 * rect.height - 2), f.value, fontsize=font_size)
                placed = True
        if not placed:
            unplaced.append((f.field_name, f.value))


def _render_template(
    form_type: str, doc: fitz.Document, fields: list[RenderField], unplaced: list[tuple[str, str]]
) -> list[float] | None:
    """Phase 2/3 template-first placement — unchanged. Returns reference_page_size
    for the appended-page fallback."""
    template = load_template(form_type)
    specs_by_name = {spec.name: spec for spec in template.required_fields}
    ref_size = template.placement.get("reference_page_size")
    default_font_size = template.placement.get("default_font_size", _DEFAULT_FONT_SIZE)
    widgets = _widget_index(doc)

    for f in fields:
        if f.value is None:
            continue  # missing / approved-blank — never invent data

        spec = specs_by_name.get(f.field_name)
        placement = spec.placement if spec else None

        acro_field = placement.get("acro_field") if placement else None
        if acro_field and acro_field in widgets:
            # AcroForm path — preferred: no pixel math, skew-immune (the widget
            # carries its own position).
            _page, widget = widgets[acro_field]
            widget.field_value = f.value
            widget.update()
            continue

        if placement and "x" in placement:
            page_index = placement["page"] - 1
            if 0 <= page_index < doc.page_count:
                page = doc[page_index]
                scale_x, scale_y = _scale(page.rect, ref_size)
                x = placement["x"] * scale_x
                y = placement["y"] * scale_y
                font_size = placement.get("font_size", default_font_size)
                # Coordinate-path limitation (see module docstring): this assumes the
                # uploaded page is flat and upright, matching reference_page_size. A
                # skewed/rotated/cropped scan will misplace this text — the AcroForm
                # branch above is preferred and skew-immune whenever available.
                page.insert_text((x, y), f.value, fontsize=font_size)
                continue

        unplaced.append((f.field_name, f.value))

    return ref_size


def render(
    form_type: str,
    fields: list[RenderField],
    blank_bytes: bytes,
    content_type: str,
    schema_source: str = "template",
) -> bytes:
    """Returns a single overlay PDF. Decrypted, full values only — the user's own
    downloaded form legitimately carries full PII (masking is a display/API concern,
    not a download concern; SPEC-PHASE3.md §8.6).

    Phase 4: `schema_source="inferred"` skips load_template entirely and places each
    field at its OWN detected bbox (RenderField.placement) instead (§7)."""
    doc = _open(blank_bytes, content_type)
    unplaced: list[tuple[str, str]] = []

    if schema_source == "inferred":
        _render_inferred(doc, fields, unplaced)
        ref_size = None
    else:
        ref_size = _render_template(form_type, doc, fields, unplaced)

    _stamp_watermark(doc)
    if unplaced:
        _append_unplaced_page(doc, unplaced, ref_size)

    return doc.tobytes()
