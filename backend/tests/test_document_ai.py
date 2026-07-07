"""document_ai.detect_fields: normalized-vertex -> normalized-bbox conversion, label
vs value region selection, and transient/terminal DocumentAIError classification
(SPEC-PHASE4.md §6.4). The GCP client itself is always mocked/faked here — no real
network call is ever exercised in CI."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.api_core import exceptions as gax_exceptions

from app.config import settings
from app.services.form_placement import document_ai


def _seg(start, end):
    return SimpleNamespace(start_index=start, end_index=end)


def _layout(text_segments=None, vertices=None, confidence=0.0):
    anchor = SimpleNamespace(text_segments=text_segments or []) if text_segments is not None else None
    poly = SimpleNamespace(normalized_vertices=vertices or []) if vertices is not None else None
    return SimpleNamespace(text_anchor=anchor, bounding_poly=poly, confidence=confidence)


def _vertex(x, y):
    return SimpleNamespace(x=x, y=y)


def _form_field(field_name, field_value=None):
    return SimpleNamespace(field_name=field_name, field_value=field_value)


def _page(form_fields):
    return SimpleNamespace(form_fields=form_fields)


def _document(text, pages):
    return SimpleNamespace(text=text, pages=pages)


@pytest.fixture(autouse=True)
def _processor_configured(monkeypatch):
    monkeypatch.setattr(settings, "documentai_processor_id", "test-processor")
    monkeypatch.setattr(settings, "documentai_project_id", "test-project")


# --- pure conversion helpers -----------------------------------------------------------


def test_layout_text_reconstructs_from_segments():
    full_text = "Father's Name: Suresh Kumar\n"
    layout = _layout(text_segments=[_seg(0, 13)])
    assert document_ai._layout_text(full_text, layout) == "Father's Name"


def test_layout_text_concatenates_multiple_segments():
    full_text = "AB CD"
    layout = _layout(text_segments=[_seg(0, 2), _seg(3, 5)])
    assert document_ai._layout_text(full_text, layout) == "ABCD"


def test_layout_text_no_anchor_returns_empty():
    layout = _layout(text_segments=None)
    assert document_ai._layout_text("anything", layout) == ""


def test_normalized_bbox_computes_min_max():
    layout = _layout(vertices=[_vertex(0.1, 0.2), _vertex(0.3, 0.2), _vertex(0.3, 0.25), _vertex(0.1, 0.25)])
    assert document_ai._normalized_bbox(layout) == (0.1, 0.2, 0.3, 0.25)


def test_normalized_bbox_no_poly_returns_none():
    layout = _layout(vertices=None)
    assert document_ai._normalized_bbox(layout) is None


# --- _fields_from_document (pure) --------------------------------------------------------


def test_fields_from_document_extracts_label_and_value_bbox():
    text = "Father's Name: Suresh Kumar"
    label_layout = _layout(text_segments=[_seg(0, 13)])
    value_layout = _layout(
        vertices=[_vertex(0.2, 0.3), _vertex(0.5, 0.3), _vertex(0.5, 0.35), _vertex(0.2, 0.35)],
        confidence=0.92,
    )
    doc = _document(text, [_page([_form_field(label_layout, value_layout)])])

    [field] = document_ai._fields_from_document(doc, page_index=1)
    assert field.name == "Father's Name"
    assert field.page == 1
    assert field.value_bbox == (0.2, 0.3, 0.5, 0.35)
    assert field.confidence == pytest.approx(0.92)


def test_fields_from_document_no_value_layout_gives_none_bbox_zero_confidence():
    text = "Some Label"
    label_layout = _layout(text_segments=[_seg(0, 10)])
    doc = _document(text, [_page([_form_field(label_layout, None)])])

    [field] = document_ai._fields_from_document(doc, page_index=1)
    assert field.value_bbox is None
    assert field.confidence == 0.0


def test_fields_from_document_skips_empty_label():
    text = ""
    label_layout = _layout(text_segments=[])
    value_layout = _layout(vertices=[_vertex(0, 0), _vertex(1, 1)], confidence=0.9)
    doc = _document(text, [_page([_form_field(label_layout, value_layout)])])

    assert document_ai._fields_from_document(doc, page_index=1) == []


def test_fields_from_document_numbers_pages_correctly():
    text = "Name"
    label_layout = _layout(text_segments=[_seg(0, 4)])
    doc = _document(text, [_page([_form_field(label_layout, None)])])

    [field] = document_ai._fields_from_document(doc, page_index=2)
    assert field.page == 2


# --- config / auth guard -----------------------------------------------------------------


def test_missing_processor_id_raises_terminal_error(monkeypatch):
    monkeypatch.setattr(settings, "documentai_processor_id", "")
    with pytest.raises(document_ai.DocumentAIError) as exc_info:
        document_ai._client()
    assert exc_info.value.transient is False


# --- error classification (real google.api_core exception types) -------------------------


def test_service_unavailable_is_transient():
    client = SimpleNamespace(
        process_document=lambda request: (_ for _ in ()).throw(gax_exceptions.ServiceUnavailable("down"))
    )
    with pytest.raises(document_ai.DocumentAIError) as exc_info:
        document_ai._process_page(client, "projects/x", b"img")
    assert exc_info.value.transient is True


def test_deadline_exceeded_is_transient():
    client = SimpleNamespace(
        process_document=lambda request: (_ for _ in ()).throw(gax_exceptions.DeadlineExceeded("timeout"))
    )
    with pytest.raises(document_ai.DocumentAIError) as exc_info:
        document_ai._process_page(client, "projects/x", b"img")
    assert exc_info.value.transient is True


def test_resource_exhausted_is_transient():
    client = SimpleNamespace(
        process_document=lambda request: (_ for _ in ()).throw(gax_exceptions.ResourceExhausted("rate limited"))
    )
    with pytest.raises(document_ai.DocumentAIError) as exc_info:
        document_ai._process_page(client, "projects/x", b"img")
    assert exc_info.value.transient is True


def test_invalid_argument_is_terminal():
    client = SimpleNamespace(
        process_document=lambda request: (_ for _ in ()).throw(gax_exceptions.InvalidArgument("bad request"))
    )
    with pytest.raises(document_ai.DocumentAIError) as exc_info:
        document_ai._process_page(client, "projects/x", b"img")
    assert exc_info.value.transient is False


def test_permission_denied_is_terminal():
    client = SimpleNamespace(
        process_document=lambda request: (_ for _ in ()).throw(gax_exceptions.PermissionDenied("no access"))
    )
    with pytest.raises(document_ai.DocumentAIError) as exc_info:
        document_ai._process_page(client, "projects/x", b"img")
    assert exc_info.value.transient is False


# --- detect_fields end-to-end (client + processor_name + process_page all mocked) ---------


def test_detect_fields_aggregates_across_pages():
    text = "Name"
    label_layout = _layout(text_segments=[_seg(0, 4)])
    doc1 = _document(text, [_page([_form_field(label_layout, None)])])
    doc2 = _document(text, [_page([_form_field(label_layout, None)])])

    with patch.object(document_ai, "_client", return_value=SimpleNamespace()), patch.object(
        document_ai, "_processor_name", return_value="projects/x/processors/y"
    ), patch.object(document_ai, "_process_page", side_effect=[doc1, doc2]):
        fields = document_ai.detect_fields([b"page1", b"page2"])

    assert [f.page for f in fields] == [1, 2]
    assert all(f.name == "Name" for f in fields)
