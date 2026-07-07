# GovForm Autofiller

An agent that ingests a citizen's ID documents once, then auto-fills government forms
from that verified profile — semantic field matching over scanned bureaucratic
paperwork, not browser-style string/attribute autofill. Every auto-filled field is
scored, cross-checked against its source document, and routed to mandatory human
review when it's below-threshold, money/legal, or a non-exact date/ID match. The
output is always a downloadable draft the user reviews and submits themselves —
**this system never submits anything to a government portal.**

See [govform-autofiller-prd.md](govform-autofiller-prd.md) for the full product spec,
[PLAN.md](PLAN.md) for build phases, and [CLAUDE.md](CLAUDE.md) for the constraints
that guide implementation.

## Quick start

```bash
cp .env.example .env          # fill in GEMINI_API_KEY and the secrets
docker compose up             # postgres, redis, minio, api (:8000), worker, frontend (:5173)
```

Backend/frontend can also run standalone — see [CLAUDE.md](CLAUDE.md) for commands.

## Known limitation: coordinate-based field placement assumes an upright scan

When a filled form is downloaded (`GET /api/forms/{id}/download`), values are drawn
onto the original uploaded form using one of two placement methods, in this order of
preference:

1. **Native AcroForm fields** — if the uploaded PDF is a fillable form with named
   fields, values are written directly into those fields. This is the preferred path:
   it has no pixel math and is **immune to scan rotation**, since each field carries
   its own position on the page.
2. **Template coordinates** — for a scanned image or a non-fillable PDF, each known
   form template (`backend/app/templates/{form_type}.json`) declares an absolute
   `(x, y)` position per field, authored against a reference page layout.

**The coordinate path assumes the uploaded scan is reasonably flat and upright.** A
significantly rotated, skewed, or heavily cropped photo/scan will cause text to land
in the wrong place, because these are static coordinates with no awareness of the
page's actual orientation.

To catch this before it silently produces a misaligned form, the fill pipeline runs a
local, best-effort rotation check (OpenCV, `backend/app/services/image_quality.py`) on
the uploaded blank form. If the estimated rotation exceeds a threshold (default 5°),
a non-blocking warning is attached to the form and surfaced on the review page —
telling you to re-scan or re-photograph the form upright for best results. **This is
advisory only: the fill still completes, the form is still reviewable and
downloadable, and the check never blocks or fails the upload.** It also never
auto-corrects the rotation — it only tells you it might be worth a re-scan.

If a form is available as a fillable PDF (one with real form fields you can click into
in a PDF reader), prefer uploading that over a photo — it sidesteps this limitation
entirely.

See `backend/app/services/form_renderer.py` for the placement/rendering code and its
inline notes on this tradeoff.
