"""Upload preprocessing: HEIC->JPEG, PDF page image extraction, downscaling, and the
error paths for undecodable/unsupported uploads."""

from __future__ import annotations

import io

import pillow_heif
import pytest
from PIL import Image
from pypdf import PdfWriter

from app.config import settings
from app.services.preprocessing import PreprocessingError, preprocess


def _jpeg_bytes(size=(200, 150), color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _heic_bytes(size=(100, 80), color=(0, 255, 0)) -> bytes:
    img = Image.new("RGB", size, color=color)
    heif_file = pillow_heif.from_pillow(img)
    buf = io.BytesIO()
    heif_file.save(buf, quality=80)
    return buf.getvalue()


def _pdf_with_embedded_image(size=(200, 150), color=(10, 20, 30)) -> bytes:
    """A single-page PDF whose page is one embedded raster image — how PIL exports an
    image to PDF, and a reasonable stand-in for a scanned ID PDF."""
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def test_plain_jpeg_passes_through():
    images, page_count = preprocess(_jpeg_bytes(), "image/jpeg")
    assert page_count == 1
    assert len(images) == 1
    out = Image.open(io.BytesIO(images[0]))
    assert out.format == "JPEG"


def test_heic_converts_to_jpeg():
    images, page_count = preprocess(_heic_bytes(), "image/heic")
    assert page_count == 1
    out = Image.open(io.BytesIO(images[0]))
    assert out.format == "JPEG"
    assert out.size == (100, 80)


def test_oversized_image_is_downscaled():
    big = _jpeg_bytes(size=(4000, 3000))
    images, _ = preprocess(big, "image/jpeg")
    out = Image.open(io.BytesIO(images[0]))
    assert max(out.size) <= 2048


def test_grayscale_image_normalized_to_rgb_or_l():
    img = Image.new("L", (100, 100), color=128)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    images, _ = preprocess(buf.getvalue(), "image/png")
    out = Image.open(io.BytesIO(images[0]))
    assert out.mode in ("RGB", "L")


def test_garbage_bytes_raise_preprocessing_error():
    with pytest.raises(PreprocessingError):
        preprocess(b"not an image at all", "image/jpeg")


def test_pdf_with_embedded_image_extracts_page():
    images, page_count = preprocess(_pdf_with_embedded_image(), "application/pdf")
    assert page_count == 1
    assert len(images) == 1
    out = Image.open(io.BytesIO(images[0]))
    assert out.format == "JPEG"


def test_pdf_too_many_pages_rejected(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_pages", 1)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(PreprocessingError, match="too many pages"):
        preprocess(buf.getvalue(), "application/pdf")


def test_pdf_with_no_embedded_images_rejected():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(PreprocessingError, match="no scannable images"):
        preprocess(buf.getvalue(), "application/pdf")
