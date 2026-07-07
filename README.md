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

## Schema inference for forms we haven't seen before

Uploading a form isn't limited to the known templates (Income Certificate,
Scholarship Application). Pick **"Other / not listed"** and type the form's name —
the system will:

1. Detect the form's own fields via **Google Document AI Form Parser** (purpose-built
   bounding-box detection — a vision-LLM is deliberately never asked for pixel
   coordinates; see `backend/app/services/ocr/vision_llm.py`'s docstring).
2. **Semantically** match each detected field label (e.g. "Father's Name" vs "Name of
   Father") to your profile data via an LLM call — not string/regex matching, which
   is exactly what breaks across the phrasing/format variance real government forms
   use.
3. Fill what it can, at a **deliberately discounted confidence** (capped by how
   confidently the label was matched), and place each value at its detected position
   on the form (or an appended "Additional fields" page if no reliable box was
   found — nothing is ever silently dropped).

**Every field on an inferred form is routed to review, with no exceptions** — even
one that verifies cleanly against your source ID document. That's not over-caution:
verification only confirms the *value* is genuinely yours (e.g. your name really is
on your Aadhaar), it can't confirm the *mapping* was right (e.g. that a detected
"Guardian's Name" field was correctly matched to your name and not, say, a parent's).
A confident mapping mistake would otherwise sail through verification undetected — a
human review is the only real backstop for that failure mode. Download stays gated
behind resolving every field, exactly as for a known-template form.

If you confidently recognize the form yourself as one of the known templates, upload
it under that type instead — the known-template path gives better field placement
and doesn't need this discounted-confidence/mandatory-review treatment. (The system
also does this automatically: if you pick "Other" but the form turns out to
confidently match a known template, it's filled from that template, not inferred.)

An inferred schema is **not** learned or cached — each upload re-runs detection and
mapping fresh. See `backend/app/agent/tools/field_mapping_tool.py` and
`backend/app/services/form_placement/document_ai.py` for the implementation, and
`SPEC-PHASE4.md` for the full design rationale.

### Document AI setup (required for schema inference)

Google Document AI uses **GCP Application Default Credentials (ADC)** — this is a
**different** mechanism from the Gemini **API key** used for OCR/vision calls
elsewhere in this project ("same GCP project" only means shared billing, not a
drop-in credential). You'll also need a **Form Parser** processor created in your GCP
project, and its id/location/project set via `DOCUMENTAI_PROCESSOR_ID`,
`DOCUMENTAI_LOCATION`, and `DOCUMENTAI_PROJECT_ID` (see `.env.example`). Without valid
credentials or a configured processor, a schema-inference upload fails cleanly with a
"could not detect any fields on this form"-style error — it never falls back to
guessing.

**Local Docker dev — one-time setup:**

1. Run `gcloud auth application-default login` on your **host** machine (not inside a
   container) and sign in with an account that has Document AI access on the project
   set in `DOCUMENTAI_PROJECT_ID`. This writes a credentials file to:
   - Windows: `%APPDATA%\gcloud\application_default_credentials.json`
   - Mac/Linux: `$HOME/.config/gcloud/application_default_credentials.json`
2. Set `GCLOUD_ADC_HOST_PATH` in your `.env` to that file's path (forward slashes on
   Windows, e.g. `C:/Users/<you>/AppData/Roaming/gcloud/application_default_credentials.json`).
3. That's it — `docker-compose.yml` mounts that file **read-only** into the `api` and
   `worker` containers at `/root/.config/gcloud/application_default_credentials.json`
   and sets `GOOGLE_APPLICATION_CREDENTIALS` to that in-container path itself, so
   `GOOGLE_APPLICATION_CREDENTIALS` should stay **blank** in your `.env` (it's only
   needed there if you run the worker directly on the host, without Docker).

If `GCLOUD_ADC_HOST_PATH` is left unset, the compose file falls back to mounting a
harmless placeholder file so the rest of the stack (Postgres/Redis/MinIO/the API
itself) still starts normally — Document AI calls just fail cleanly until you
complete the steps above. Re-run `gcloud auth application-default login`
periodically; ADC tokens expire.

**To verify it's working after `docker compose down && docker compose up --build`:**
watch the `worker` container logs. `fill_form_task` never logs raw Document AI
responses (no PII in logs), but a credentials problem surfaces as a `fill_form_task
failed` log line with a safe reason like `"could not detect any fields on this
form"` (auth/permission errors are treated as terminal, not retried) — if you instead
see repeated retry attempts with a `"...temporarily unavailable"` reason, that's a
transient network/quota issue rather than a credentials one. Uploading a form under
**"Other / not listed"** and confirming it reaches the review page with at least one
detected field (rather than immediately failing) is the practical end-to-end check.
