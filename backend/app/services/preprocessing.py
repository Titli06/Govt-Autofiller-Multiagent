"""Upload preprocessing: normalize whatever the user uploaded into a list of clean JPEG
page images ready for the vision-LLM.

Handles: HEIC/HEIF (mobile camera) -> JPEG via pillow-heif, EXIF orientation correction,
downscaling of oversized images, and PDFs. Scanned government ID PDFs are almost always a
single full-page raster image embedded per page, so PDF pages are handled by extracting
their largest embedded image rather than rasterizing the page (which would need a poppler
binary this project doesn't depend on) — a genuinely vector/text PDF is out of scope and
raises PreprocessingError.
"""

from __future__ import annotations

import io

import pillow_heif
from PIL import Image, ImageOps
from pypdf import PdfReader

from app.config import settings

pillow_heif.register_heif_opener()

_MAX_DIMENSION = 2048  # downscale cap — vision-LLMs gain nothing from larger images
_JPEG_QUALITY = 90


class PreprocessingError(Exception):
    """Raised when the uploaded bytes can't be decoded into usable page images."""


def preprocess(data: bytes, content_type: str) -> tuple[list[bytes], int]:
    """Returns (normalized JPEG page images, page_count)."""
    try:
        if content_type == "application/pdf":
            return _preprocess_pdf(data)
        return _preprocess_image(data)
    except PreprocessingError:
        raise
    except Exception as exc:
        raise PreprocessingError(f"could not decode document: {type(exc).__name__}") from exc


def _normalize_image(img: Image.Image) -> bytes:
    img = ImageOps.exif_transpose(img) or img  # normalize orientation from EXIF
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > _MAX_DIMENSION:
        img.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


def _preprocess_image(data: bytes) -> tuple[list[bytes], int]:
    img = Image.open(io.BytesIO(data))
    img.load()
    return [_normalize_image(img)], 1


def _preprocess_pdf(data: bytes) -> tuple[list[bytes], int]:
    reader = PdfReader(io.BytesIO(data))
    page_count = len(reader.pages)
    if page_count == 0:
        raise PreprocessingError("PDF has no pages")
    if page_count > settings.max_upload_pages:
        raise PreprocessingError(f"PDF has too many pages (max {settings.max_upload_pages})")

    images: list[bytes] = []
    for page in reader.pages:
        embedded = list(page.images)
        if not embedded:
            continue
        # Scanned ID PDFs are typically one full-page image per page — take the largest
        # embedded image as that page's content.
        largest = max(embedded, key=lambda img: len(img.data))
        page_img = Image.open(io.BytesIO(largest.data))
        page_img.load()
        images.append(_normalize_image(page_img))

    if not images:
        raise PreprocessingError(
            "no scannable images found in PDF (text-only/vector PDFs are not supported)"
        )
    return images, page_count
