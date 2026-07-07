"""image_quality.estimate_skew: the OpenCV skew/rotation sanity check (SPEC-PHASE3.md
§8.4.4). Exercised against synthetic fixtures with a known dominant line angle —
horizontal lines for an upright "page", rotated lines for a skewed one — rather than
real scans, since what matters here is the angle math, not real-world OCR quality."""

from __future__ import annotations

import io
import math

import pytest
from PIL import Image, ImageDraw

from app.services.image_quality import estimate_skew


def _lined_image(angle_deg: float, size=(300, 300)) -> bytes:
    """A page-like image whose only content is a set of parallel lines at
    `angle_deg` from horizontal — mimics text baselines / form rule lines."""
    img = Image.new("L", size, color=255)
    draw = ImageDraw.Draw(img)
    cx, cy = size[0] // 2, size[1] // 2
    rad = math.radians(angle_deg)
    for offset in range(-140, 140, 10):
        x0 = cx - 140 * math.cos(rad) + offset * math.sin(rad)
        y0 = cy - 140 * math.sin(rad) - offset * math.cos(rad)
        x1 = cx + 140 * math.cos(rad) + offset * math.sin(rad)
        y1 = cy + 140 * math.sin(rad) - offset * math.cos(rad)
        draw.line([(x0, y0), (x1, y1)], fill=0, width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_upright_lines_estimate_near_zero_skew():
    angle = estimate_skew(_lined_image(0))
    assert abs(angle) < 1.0


def test_rotated_lines_estimate_matches_known_angle():
    angle = estimate_skew(_lined_image(12))
    assert angle == pytest.approx(12, abs=2.0)


def test_negative_rotation_detected_with_correct_sign():
    angle = estimate_skew(_lined_image(-8))
    assert angle == pytest.approx(-8, abs=2.0)


def test_undecodable_bytes_return_zero_not_an_exception():
    assert estimate_skew(b"not an image") == 0.0


def test_blank_image_with_no_lines_returns_zero():
    img = Image.new("L", (100, 100), color=255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert estimate_skew(buf.getvalue()) == 0.0
