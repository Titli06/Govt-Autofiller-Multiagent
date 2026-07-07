"""form_renderer: deterministic, template-first overlay rendering (SPEC-PHASE3.md
§8.4). Uses real PyMuPDF against small synthetic fixtures (a blank image, a plain
coordinate PDF, and an AcroForm PDF built in-memory) — no mocking of fitz itself,
since getting this pixel/points math right is exactly what needs testing."""

from __future__ import annotations

import io

import fitz
import pytest
from PIL import Image

import app.services.form_renderer as form_renderer
from app.agent.tools.form_schema_tool import Template, TemplateField
from app.services.form_renderer import RenderField, render


def _jpeg_bytes(size=(200, 200)) -> bytes:
    img = Image.new("RGB", size, color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _pdf_bytes(size=(200, 200)) -> bytes:
    doc = fitz.open()
    doc.new_page(width=size[0], height=size[1])
    return doc.tobytes()


def _acroform_pdf_bytes(field_name="ApplicantName", size=(200, 200)) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=size[0], height=size[1])
    widget = fitz.Widget()
    widget.field_name = field_name
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(10, 10, 150, 30)
    page.add_widget(widget)
    return doc.tobytes()


def _use_template(monkeypatch, template: Template) -> None:
    monkeypatch.setattr(form_renderer, "load_template", lambda form_type: template)


def _coord_template(fields=None, ref_size=(200, 200)):
    fields = fields or [
        TemplateField(
            name="applicant_name",
            profile_key="full_name",
            high_stakes=False,
            placement={"page": 1, "x": 10, "y": 20},
        )
    ]
    return Template(
        form_type="test_form",
        display_name="Test Form",
        required_fields=fields,
        placement={"reference_page_size": list(ref_size), "default_font_size": 10},
    )


def test_coordinate_placement_produces_single_page_pdf_no_unplaced(monkeypatch):
    _use_template(monkeypatch, _coord_template())
    fields = [RenderField(field_name="applicant_name", value="Ravi Kumar")]

    out = render("test_form", fields, _pdf_bytes(), "application/pdf")

    assert out.startswith(b"%PDF")
    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 1  # no unplaced fields -> no appended page
    text = doc[0].get_text()
    assert "Ravi Kumar" in text


def test_watermark_stamped_on_every_page(monkeypatch):
    _use_template(monkeypatch, _coord_template())
    fields = [RenderField(field_name="applicant_name", value="Ravi Kumar")]

    out = render("test_form", fields, _pdf_bytes(), "application/pdf")
    doc = fitz.open(stream=out, filetype="pdf")
    assert "DRAFT" in doc[0].get_text()


def test_missing_and_approved_blank_fields_never_appear(monkeypatch):
    _use_template(monkeypatch, _coord_template())
    fields = [RenderField(field_name="applicant_name", value=None)]

    out = render("test_form", fields, _pdf_bytes(), "application/pdf")
    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 1
    assert "Ravi Kumar" not in doc[0].get_text()


def test_unmatched_field_lands_on_appended_additional_fields_page(monkeypatch):
    _use_template(monkeypatch, _coord_template())
    fields = [
        RenderField(field_name="applicant_name", value="Ravi Kumar"),
        RenderField(field_name="mystery_field", value="Surprise Value"),
    ]

    out = render("test_form", fields, _pdf_bytes(), "application/pdf")
    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 2
    assert "Additional fields" in doc[1].get_text()
    assert "Surprise Value" in doc[1].get_text()


def test_acroform_widget_filled_when_matched(monkeypatch):
    template = _coord_template(
        fields=[
            TemplateField(
                name="applicant_name",
                profile_key="full_name",
                high_stakes=False,
                placement={"acro_field": "ApplicantName"},
            )
        ]
    )
    _use_template(monkeypatch, template)
    fields = [RenderField(field_name="applicant_name", value="Ravi Kumar")]

    out = render("test_form", fields, _acroform_pdf_bytes(), "application/pdf")
    doc = fitz.open(stream=out, filetype="pdf")
    widget = next(iter(doc[0].widgets()))
    assert widget.field_value == "Ravi Kumar"


def test_acroform_field_not_present_in_upload_falls_back_to_unplaced(monkeypatch):
    template = _coord_template(
        fields=[
            TemplateField(
                name="applicant_name",
                profile_key="full_name",
                high_stakes=False,
                placement={"acro_field": "SomeOtherFieldName"},
            )
        ]
    )
    _use_template(monkeypatch, template)
    fields = [RenderField(field_name="applicant_name", value="Ravi Kumar")]

    out = render("test_form", fields, _acroform_pdf_bytes(), "application/pdf")
    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 2
    assert "Ravi Kumar" in doc[1].get_text()


def test_image_upload_opens_as_single_page_pdf(monkeypatch):
    _use_template(monkeypatch, _coord_template())
    fields = [RenderField(field_name="applicant_name", value="Ravi Kumar")]

    out = render("test_form", fields, _jpeg_bytes(), "image/jpeg")
    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 1
    assert "Ravi Kumar" in doc[0].get_text()


def test_schema_source_defaults_to_template_regression(monkeypatch):
    """render() called without schema_source (as every pre-Phase-4 caller does) must
    behave exactly as the template path always has."""
    _use_template(monkeypatch, _coord_template())
    fields = [RenderField(field_name="applicant_name", value="Ravi Kumar")]

    out = render("test_form", fields, _pdf_bytes(), "application/pdf")
    doc = fitz.open(stream=out, filetype="pdf")
    assert "Ravi Kumar" in doc[0].get_text()


# --- Phase 4: inferred-schema placement (SPEC-PHASE4.md §7) ---------------------------


def test_inferred_places_value_at_its_own_normalized_bbox(monkeypatch):
    fields = [
        RenderField(
            field_name="father_s_name",
            value="Suresh Kumar",
            placement={"page": 1, "bbox": [0.1, 0.1, 0.6, 0.15]},
        )
    ]

    out = render("marriage_certificate", fields, _pdf_bytes(), "application/pdf", schema_source="inferred")

    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 1  # no unplaced fields -> no appended page
    assert "Suresh Kumar" in doc[0].get_text()


def test_inferred_does_not_call_load_template(monkeypatch):
    def _boom(form_type):
        raise AssertionError("load_template must not be called on the inferred path")

    monkeypatch.setattr(form_renderer, "load_template", _boom)
    fields = [
        RenderField(field_name="father_s_name", value="Suresh Kumar", placement={"page": 1, "bbox": [0.1, 0.1, 0.6, 0.15]})
    ]

    out = render("marriage_certificate", fields, _pdf_bytes(), "application/pdf", schema_source="inferred")
    assert out.startswith(b"%PDF")


def test_inferred_field_with_no_placement_lands_on_additional_fields_page():
    fields = [
        RenderField(field_name="father_s_name", value="Suresh Kumar", placement={"page": 1, "bbox": [0.1, 0.1, 0.6, 0.15]}),
        RenderField(field_name="purpose", value="Marriage registration", placement=None),
    ]

    out = render("marriage_certificate", fields, _pdf_bytes(), "application/pdf", schema_source="inferred")

    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 2
    assert "Additional fields" in doc[1].get_text()
    assert "Marriage registration" in doc[1].get_text()


def test_inferred_field_missing_value_never_appears():
    fields = [RenderField(field_name="father_s_name", value=None, placement={"page": 1, "bbox": [0.1, 0.1, 0.6, 0.15]})]

    out = render("marriage_certificate", fields, _pdf_bytes(), "application/pdf", schema_source="inferred")

    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 1
    assert "Suresh Kumar" not in doc[0].get_text()


def test_inferred_out_of_range_page_lands_on_additional_fields_page():
    fields = [
        RenderField(field_name="father_s_name", value="Suresh Kumar", placement={"page": 5, "bbox": [0.1, 0.1, 0.6, 0.15]})
    ]

    out = render("marriage_certificate", fields, _pdf_bytes(), "application/pdf", schema_source="inferred")

    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 2
    assert "Suresh Kumar" in doc[1].get_text()


def test_inferred_bbox_scales_to_actual_page_size():
    # A bbox normalized against ANY reference authored size scales to the actual
    # upload page (0.5 -> the horizontal midpoint of whatever page this actually is).
    fields = [
        RenderField(field_name="father_s_name", value="Suresh Kumar", placement={"page": 1, "bbox": [0.5, 0.1, 0.9, 0.15]})
    ]

    out = render(
        "marriage_certificate", fields, _pdf_bytes(size=(400, 400)), "application/pdf", schema_source="inferred"
    )

    doc = fitz.open(stream=out, filetype="pdf")
    text_instances = doc[0].search_for("Suresh Kumar")
    assert len(text_instances) == 1
    assert text_instances[0].x0 == pytest.approx(200, abs=5)  # 0.5 * 400


def test_coordinate_scaling_when_upload_page_differs_from_reference_size(monkeypatch):
    # Reference authored at 100x100; actual upload is 200x200 (2x). Placing at (10, 20)
    # should scale to (20, 40) on the real page — verified indirectly via no exception
    # and the text still landing inside the actual (larger) page bounds.
    template = _coord_template(ref_size=(100, 100))
    _use_template(monkeypatch, template)
    fields = [RenderField(field_name="applicant_name", value="Ravi Kumar")]

    out = render("test_form", fields, _pdf_bytes(size=(200, 200)), "application/pdf")
    doc = fitz.open(stream=out, filetype="pdf")
    text_instances = doc[0].search_for("Ravi Kumar")
    assert len(text_instances) == 1
    rect = text_instances[0]
    assert rect.x0 == pytest.approx(20, abs=2)
