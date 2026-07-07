"""Input-quality guard: OpenCV skew/rotation sanity check on an uploaded blank form.

Coordinate-based field placement (services/form_renderer.py) assumes a reasonably
flat, upright scan authored against the template's reference page layout. A
significantly rotated scan silently misplaces absolute-coordinate text. This module
estimates the dominant page rotation so fill_form_task can surface a non-blocking
`Form.placement_warning` (SPEC-PHASE3.md Decision 15, §8.4.4) — it never blocks the
fill, and the AcroForm placement path is unaffected (a widget carries its own
position regardless of scan rotation).

Best-effort by design: the caller must swallow any exception from `estimate_skew` —
a detector failure must never fail the fill over a quality heuristic. Purely local
(OpenCV only); no network call, nothing sent to a third party for this check.
"""

from __future__ import annotations

import cv2
import numpy as np


def estimate_skew(image_bytes: bytes) -> float:
    """Returns the estimated dominant page rotation in degrees, roughly in
    [-45, 45] (0 = upright). Detects straight edges (Canny) and their dominant angle
    (Hough line transform) — text baselines and form rule lines are reliably straight
    on an upright scan and reliably tilted on a rotated one.

    Returns 0.0 when the image can't be decoded or no reliable line signal is found
    (e.g. a very sparse/blank page) — that is a "nothing to warn about" result, not
    an error; callers should not distinguish it from a genuinely upright scan.
    """
    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return 0.0

    edges = cv2.Canny(image, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=150)
    if lines is None:
        return 0.0

    angles = []
    for line in lines:
        _rho, theta = line[0]
        # theta is measured from the x-axis; a perfectly horizontal line has
        # theta == 90 deg (0 skew). Normalize so near-vertical lines (theta ~ 0/180,
        # e.g. a form's vertical rules) don't wrap around to +-90.
        angle_deg = float(np.degrees(theta) - 90)
        if angle_deg > 45:
            angle_deg -= 90
        elif angle_deg < -45:
            angle_deg += 90
        angles.append(angle_deg)

    if not angles:
        return 0.0
    return float(np.median(angles))
