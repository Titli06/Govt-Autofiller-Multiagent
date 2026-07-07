# SPEC — Phase 3: Verification + HITL Review + Download

Scope-locked spec for **Phase 3 only** of [PLAN.md](PLAN.md). Ships the safety-critical vertical
slice — DB → LangGraph agent → async worker → API → frontend — that **closes the trust loop**:
each mapped/formatted form value is re-verified against its source document, every flagged field
is forced through a **required, download-blocking** human review, and the approved result is
rendered as a **downloadable filled form overlaid on the original** — never submitted anywhere.

> **Authority:** [PLAN.md](PLAN.md) Phase 3 + [govform-autofiller-prd.md](govform-autofiller-prd.md)
> UC4 (human review of flagged fields), UC7 (cross-check before finalizing), FR7 (never
> auto-submit), FR8 (mandatory HITL for below-threshold / money-legal / non-exact date-ID),
> FR9 (green/yellow/red review UI), §6 (data-flow steps 4–8), §8 (Usability/Auditability NFRs),
> §10 (hallucination + "users treating auto-fill as final" risks), and [CLAUDE.md](CLAUDE.md)
> (confidence grounded in **source-document match**, not LLM self-report; mandatory HITL is a
> required blocking step; every filled field auditable to source + score; **never** auto-submit).
>
> Builds on Phase 0 ([SPEC.md](SPEC.md)) — auth, `get_current_user`, sync SQLAlchemy 2.0, Celery;
> Phase 1 ([SPEC-PHASE1.md](SPEC-PHASE1.md)) — encrypted multi-candidate `ProfileField` store with
> per-candidate **source snippet**, `core/encryption.py` (AES-256-GCM + masking),
> `core/validators.py`, `services/storage.py`, `services/preprocessing.py`, the Gemini vision
> provider, and `GET /documents/{id}/file`; Phase 2 ([SPEC-PHASE2.md](SPEC-PHASE2.md)) — the
> `Form`/`FormField` tables, the `build_graph()` pipeline (`form_schema → profile_lookup →
> confidence_scorer`), `profile_lookup_tool`, `confidence_scorer_tool`, `fill_form_task`,
> `classify_form`, and the read-only draft view this phase makes reviewable.
>
> Where PLAN and PRD are silent, the decisions in §2 were made in the Phase 3 build interview and
> are **binding for this phase**.

---

## 1. Objectives & Done-When

**Done when:** a form that has flagged fields **cannot be downloaded** until the user
approves/corrects each; verification has re-checked every filled value against its source ID
document (catching format-conversion drift the Phase 1/2 confidence alone would miss); and the
approved output downloads as a **filled PDF overlaid on the original form**, never submitted.

Acceptance is enumerated in §14.

### In scope
- **Agent:** implement `agent/tools/document_verification_tool.py` and insert a
  `document_verification` node **between** `profile_lookup` and `confidence_scorer` in
  `agent/graph.py` (the exact seam Phase 2 left). Extend `confidence_scorer_tool` to fold the
  verification result into the final score, flags, and review-reason precedence.
- **Vision:** add `verify_value_on_document(images, value)` (does this value appear on the source
  ID doc?) to the Gemini provider. (No LLM coordinate detection — placement is deterministic; §8.4.)
- **DB:** Phase-3 migration `0004_verification_review` — new `FormField` columns (`verified`,
  `verification_method`, `corrected_value_encrypted`, `review_action`, `reviewed_at`), new
  `Form.rendered_s3_key`; and a Phase-1-model relaxation to support manual profile candidates
  (`profile_fields.source_doc_id` → nullable + `origin`).
- **Worker:** extend `fill_form_task` — snapshot now carries source snippets; inject the verifier
  callable; persist verification results; terminal status is now **`in_review`** (flags present)
  or **`approved`** (none), not `filled`.
- **Rendering:** implement `services/form_renderer.py` — overlay the approved values onto the
  original uploaded form → PDF using **deterministic template placement** (AcroForm-first, then
  template coordinates; §8.4) with **PyMuPDF**; persist to S3; reference from
  `Form.rendered_s3_key`. Field-detection fallback for template-less forms (Google Document AI)
  is **interface-only here, integrated in Phase 4**.
- **Input-quality guard:** a basic **OpenCV skew/rotation sanity check** on the uploaded blank form
  during fill preprocessing → a **non-blocking** `placement_warning` on the `Form`, surfaced in the
  review UI and before download (coordinate placement assumes a reasonably flat, upright scan;
  §8.4.4). **Document this limitation** in `README.md` and in code comments by the coordinate-filling
  logic (`services/form_renderer.py`), and note that AcroForm-based filling is the preferred,
  skew-immune path.
- **API:** `GET /api/forms/{id}/review`, `POST /api/forms/{id}/review` (per-field
  approve/correct/approve-blank + optional profile write-back), `GET /api/forms/{id}/download`
  (blocked until `approved`), `GET /api/forms/{id}/file` (serve the blank form for side-by-side).
- **Frontend:** a **Review page** — confidence-coded fields (green verified / yellow low-conf /
  red missing/high-stakes/verification-failed), one-click approve/edit/approve-blank per field,
  side-by-side with the source document, an optional "also save to my profile" per correction,
  and a **download button disabled until review is complete**.

### Out of scope (defer)
- **Schema inference** for unseen forms (`form_schema_tool` inference branch) — **Phase 4**. A
  form with no template is still rejected/uploaded only against the two known types.
- **History / reuse dashboard** (`GET /api/history`) and the **deletion cascade** — **Phase 5**.
  (Phase 3 *does* touch the Phase-1 profile model to allow manual write-back candidates; the
  cascade/purge remains Phase 5.)
- **Metrics dashboards** — **Phase 6**. Phase 3 makes verification pass/fail rate and
  review-time **recoverable** (§10), not dashboarded.
- **Google Document AI field-detection fallback** (for template-less forms) — **interface + config
  defined in Phase 3, real integration in Phase 4** (schema inference is the same "no template"
  trigger, and Phase 3 upload `422`s unknown types, so the path is unreachable here; §8.4).
- **Coordinate-perfect typography / multi-column reflow** in the overlay — Phase 3 places a
  single text value per template-defined coordinate/AcroForm field; complex table/grid forms
  degrade to the appended "unplaced fields" page (§8.4), not a hard failure.
- **Auto-submit** — never, in any phase (FR7).

---

## 2. Decisions carried from the interview (binding for Phase 3)

| # | Area | Decision |
|---|---|---|
| 1 | **Verification mechanism** | **Hybrid.** For each fillable field, first do a **deterministic** re-ground of the *formatted form value* against the candidate's stored source snippet (`snippet_contains` + typed semantic equality for dates/IDs). On a deterministic **mismatch/ambiguous**, escalate to a **vision-LLM** check against the source ID document image. Deterministic pass ⇒ no LLM call. |
| 2 | **Verification scope** | **Every fillable field**, including fields backed by a `user_confirmed`/`user_corrected` profile candidate (a trusted profile value can still be mis-formatted onto the form). Missing fields (`no_mapping`/`no_candidate`) are **not** verified — there is nothing to check; they stay flagged missing. |
| 3 | **Verification effect** | **Gate that both confirms and rejects.** A verified **exact** match sets `verified=true` and grounds the score **high**; a **semantic/LLM** match sets `verified=true` but does **not** promote the inherited (capped) score. A **failure** (deterministic miss *and* LLM says no) sets `verified=false`, drops confidence to **low**, and flags `verification_failed` as the **new top-precedence** review reason. **High-stakes fields still always route to review** regardless of verification (FR8). |
| 4 | **Review-reason precedence** | New order: **`missing` > `verification_failed` > `high_stakes` > `unverified_source` > `low_confidence`.** `transformed` is still never a reason. |
| 5 | **Download artifact** | **Overlay on the original form → PDF.** Draw each approved value onto the uploaded blank form (image or PDF) at its template-defined coordinate / AcroForm field (§8.4); output a single PDF. The user's own downloaded form contains **full** values (not masked — masking is a display/third-party concern, §8.2). |
| 6 | **Field placement** | **Template-first deterministic; AI fallback only for template-less forms (Phase 4).** Each known template declares per-field placement — a named **AcroForm field** (preferred when the blank form is a fillable PDF) or absolute **(x, y) + font size** coordinates. The renderer writes values deterministically with **PyMuPDF (`fitz`)** — **no AI call**. For a form with **no** template, **Google Document AI Form Parser** detects field boxes (purpose-built; far more reliable than vision-LLM coordinate output) — **interface defined here, real integration deferred to Phase 4** (unreachable in Phase 3, where upload `422`s unknown types). A field with no matched AcroForm name and no coordinates goes to the appended "unplaced fields" page (§8.4). *(Revised from an earlier LLM-coordinate approach — LLMs aren't built for precise pixel output.)* |
| 7 | **Render timing** | **Lazy + cached.** Render on the **first** `GET /download`, persist the PDF to S3, reference it from `Form.rendered_s3_key`. Any later change to the approved value set clears the cache so it regenerates. |
| 8 | **Status lifecycle** | **`in_review → approved`, auto-approve when zero flags.** Pipeline success lands `in_review` if any field needs review, else straight to `approved`. Resolving every outstanding flagged field auto-transitions `in_review → approved`. Download unlocks **only** in `approved`. `filled` is **retired** as a terminal status (replaced by `in_review`/`approved`). |
| 9 | **Edit semantics** | **One-shot pipeline; edits re-open review.** The fill+verify pipeline runs once (`pending → in_review`/`approved`) and is not re-triggered on a terminal form. **Correcting** a field on an already-`approved` form re-opens that field (`reviewed=false`), drops the form back to `in_review`, and **invalidates** `rendered_s3_key`. Re-processing from scratch means a fresh upload (new `Form`). |
| 10 | **Corrections write-back** | **Ask per correction.** `POST /review` (correct) takes `propagate_to_profile: bool`. When true and the field was filled from a **real profile candidate** → update that candidate (Phase-1 `/correct` semantics: convert to canonical, validate, `status=user_corrected`, confidence 1.0). When true and the field was **missing** (hand-typed) → **synthesize a manual profile candidate** (Decision 11). |
| 11 | **Manual write-back** | **Synthesize a manual candidate.** A hand-typed value for a missing field, with `propagate_to_profile=true`, creates a new `ProfileField` tagged `origin="manual"` with `source_doc_id=NULL`. Requires relaxing Phase-1's `source_doc_id` NOT NULL (§4.3). Only offered when the form's `profile_key` is non-null (a canonical target exists); a `no_mapping` field cannot write back. |
| 12 | **Correction validation** | **Validate typed/high-stakes, free-text as-is.** A corrected `dob`/`aadhaar_number`/`pan_number` is format-validated (in the **form's** expected format for dates); names/addresses/income accepted verbatim. A human correction becomes `verified=true`, `reviewed=true`, `confidence=1.0`, `verification_method="user"`. Invalid → `422`. |
| 13 | **Blank fields** | **Approve-as-blank allowed.** A field the user has no data for can be explicitly acknowledged (`action="approve_blank"`): `reviewed=true`, `value` stays null, recorded as `review_action="approved_blank"`. Download is **not** blocked by an acknowledged-blank field. We never invent data. |
| 14 | **Spec location** | This file — `SPEC-PHASE3.md`. Earlier specs unchanged; PLAN's Phase 3 heading links here. |
| 15 | **Input-quality guard (skew/rotation)** | **Warn, never block.** A basic OpenCV skew/rotation check runs on the uploaded blank form during fill preprocessing. If the dominant page rotation exceeds `skew_warn_degrees` (~5°), a **non-blocking** `placement_warning` (+ `skew_angle`) is recorded on the `Form` and surfaced in the review UI and before download — telling the user to re-scan/re-photograph rather than risk silently misplaced **coordinate-based** fields. The fill still completes normally; the **AcroForm path is skew-immune**, so the warning is advisory only for the coordinate path. Best-effort (a detector failure never fails the fill). The limitation is documented in `README.md` and in code comments by the coordinate-filling logic. |

### Default implementation choices (not interviewed; set here)
- **Verifier fetches source images lazily, cached per document.** The worker builds the `verifier`
  callable as a closure that, on escalation, fetches the field's `source_doc_id` document bytes
  from S3, preprocesses to images (memoized per doc within the run), and calls
  `vision_llm.verify_value_on_document`. Transient LLM/S3 errors during verification propagate as
  a retryable failure (task retry), never a silent pass.
- **Graph nodes stay pure over `(state, config)`.** The new external input — the `verifier`
  callable — is injected via `config["configurable"]`, exactly like Phase 2's
  `snapshot`/`images`/`classifier`. The nodes/tools remain DB- and crypto-free and testable with
  stubs. (Field placement is **not** a graph concern; it happens deterministically at render time.)
- **Verification runs on the resolved form type only** (never on a `type_mismatch`, which
  short-circuits before `profile_lookup` as in Phase 2).
- **Per-field review, not bulk.** `POST /review` takes **one** field action per call (safer for
  the elderly/low-literacy persona; FR8 wants deliberate review). The frontend calls it per
  field. No "approve all" in Phase 3.
- **`reviewed` is per-field; `approved` is the form derived from it.** A field is *outstanding*
  iff `needs_review AND NOT reviewed`. The form is `approved` iff it is in the review lifecycle
  and has **zero** outstanding fields; else `in_review`. Non-flagged fields never need action.
- **DB access / IDs / timestamps:** same as Phases 0–2 — sync SQLAlchemy 2.0, `psycopg` v3, UUIDv4
  PKs, `TIMESTAMP WITH TIME ZONE` UTC with `now()` server defaults.

---

## 3. Verification: the trust layer (`document_verification_tool`)

The core of Phase 3. It replaces the stub in
[`agent/tools/document_verification_tool.py`](backend/app/agent/tools/document_verification_tool.py)
and runs **after** `profile_lookup` (which selected + formatted the value) and **before**
`confidence_scorer` (which prices the result). It is **pure** over its inputs plus an injected
`verifier` callable.

### 3.1 Inputs
`profile_lookup_tool` is extended to carry the chosen candidate's **source snippet** forward, so
verification can re-ground deterministically without DB/crypto:

- each lookup dict gains `candidate_snippet: str | None` (decrypted snippet from the selected
  `CandidateView`; `None` for missing fields or a candidate with no stored snippet).

The verification node receives, via config: `verifier: Callable[[str, str], bool]` —
`(value, source_doc_id) -> matches`, injected by the worker (LLM escalation path).

### 3.2 Algorithm (per field)
```
if field.missing is not None:            # no_mapping / no_candidate
    verified = False; method = None      # nothing to verify — leave the missing flag to scorer
    continue

value   = field.value                    # already selected + formatted (§Phase 2)
snippet = field.candidate_snippet

# 1) DETERMINISTIC re-ground of the *formatted* value against the source snippet.
det = deterministic_match(field.profile_key, value, snippet)   # -> "exact" | "semantic" | "miss"

if det == "exact":
    verified = True;  method = "exact"           # promote to high in the scorer
elif det == "semantic":
    verified = True;  method = "semantic"        # date/ID parse-equal but not byte-equal — keep score
else:  # "miss" (including snippet missing/empty)
    # 2) ESCALATE to the vision-LLM against the source document image.
    if verifier(value, field.source_doc_id):     # may raise VisionExtractionError (transient -> retry)
        verified = True;  method = "llm"          # matched, but not a clean deterministic match -> keep score
    else:
        verified = False; method = "llm"          # genuine mismatch -> verification_failed (scorer)
```

`deterministic_match(profile_key, value, snippet)`:
- **dates** (`profile_key == "dob"`): `exact` if `snippet_contains(value, snippet)`; else `semantic`
  if the value parses (form's `%d/%m/%Y`) and the snippet contains a date that parses to the
  **same calendar day** (reuse `core/validators.parse_dob` over the snippet); else `miss`. This is
  what catches a **format-conversion bug** — a swapped `DD/MM` no longer matches its own snippet.
- **IDs** (`aadhaar_number`, `pan_number`): `exact` if `snippet_contains(value, snippet)`; else
  `semantic` if the normalized value (`normalize_aadhaar`/`normalize_pan`) appears in the
  normalized snippet; else `miss`.
- **free text** (name/address, etc.): `exact` if `snippet_contains(value, snippet)` (already
  casefold + whitespace-normalized, so `upper`/`single_line` transforms still match); else `miss`
  (no semantic tier).
- **empty/None snippet** → `miss` (forces LLM escalation rather than a false pass).

### 3.3 Output
Each field dict gains: `verified: bool`, `verification_method: str | None`
(`"exact" | "semantic" | "llm" | "user" | None`). The `confidence_scorer_tool` (below) consumes
these; the tool itself does **not** set confidence or review flags — that policy stays in one
place (the scorer), as in Phase 2.

> **Why deterministic-first:** it is free, unit-testable, and aligns with CLAUDE.md's "confidence
> grounded in source-document match, not LLM self-report." The LLM is a *fallback* to avoid
> false-flagging on snippet gaps, not the primary signal. A transient LLM error retries the job;
> it never resolves to "verified."

---

## 4. Data Model changes

One Phase-3 migration `0004_verification_review`. `db/base.py` already imports `Form`/`FormField`;
the new columns ride the existing models.

### 4.1 `forms` — additions
| Column | Type | Notes |
|---|---|---|
| `rendered_s3_key` | text, nullable | cached overlay-PDF object key; `NULL` until first download or after invalidation (Decision 7/9) |
| `skew_angle` | float, nullable | dominant page rotation (degrees) estimated at fill time; `NULL` if not measured (Decision 15) |
| `placement_warning` | text, nullable | **safe, non-PII** advisory when the scan is significantly skewed (coordinate placement may be off); `NULL` when the page looks upright or on the AcroForm path (Decision 15) |

`status` (existing `String(32)`) gains values **`in_review`** and **`approved`**; `filled` is no
longer produced (`pending → processing → (in_review \| approved \| failed \| type_mismatch)`). No
DB enum type — it is a string column, so no migration beyond documentation.

### 4.2 `form_fields` — additions
| Column | Type | Notes |
|---|---|---|
| `verified` | bool, not null, default false | set by verification (§3); `false` for missing/failed |
| `verification_method` | text, nullable | `exact` \| `semantic` \| `llm` \| `user` \| null |
| `corrected_value_encrypted` | bytea, nullable | AES-256-GCM of the user's corrected value; effective value = corrected if present else auto-filled (mirrors `ProfileField`) |
| `review_action` | text, nullable | `approved` \| `corrected` \| `approved_blank` \| null (unreviewed) |
| `reviewed_at` | timestamptz, nullable | when the user resolved this field (review-time metric seam) |

Existing `reviewed` (bool, default false) is now **written** by Phase 3. Add an
`effective_value_encrypted` property on `FormField` (returns `corrected_value_encrypted or
value_encrypted`), matching `ProfileField`.

### 4.3 `profile_fields` — relaxation for manual write-back (Decision 11)
| Column | Change | Notes |
|---|---|---|
| `source_doc_id` | `NOT NULL → NULLABLE` (FK unchanged, `ON DELETE CASCADE`) | a manual candidate has no source document |
| `origin` | **new** text, not null, default `"document"` | `"document"` (OCR-extracted, Phase 1) \| `"manual"` (hand-typed in review) |

Consequences to handle (Phase-1 code touch-points, enumerated in §11):
- The `uq_profile_field_candidate (profile_id, field_name, source_doc_id)` unique constraint: with
  a `NULL` `source_doc_id`, Postgres treats rows as distinct, so multiple manual candidates for one
  `field_name` are permitted. Acceptable (multi-candidate model); no extra constraint needed.
- `fill_form_task._build_profile_snapshot` currently **inner-joins** `ProfileField → Document`;
  change to an **outer join** so manual candidates (null `source_doc_id`) are included, with
  `doc_type` reported as `"manual"`/`None`.
- `profile.py::_get_source_document` asserts the doc is non-null; guard for manual candidates
  (return a `None`/`"manual"` source in `ProfileFieldOut`), so the Profile page doesn't 500 on a
  manual field.

---

## 5. Config, deps & env additions

`config.py` — reuse Phase-1/2 knobs (`confidence_threshold=0.90`, `ocr_confidence_high/_medium`,
`fill_*` retry knobs, S3, Gemini). Add:
```
# Verification (Phase 3)
verify_low_confidence: float = 0.30      # score a verification_failed field drops to (band: low)

# Overlay rendering (Phase 3)
render_watermark_text: str = "DRAFT — NOT SUBMITTED"

# Input-quality guard (Phase 3) — coordinate placement assumes a flat, upright scan.
skew_warn_degrees: float = 5.0           # |dominant rotation| above this → placement_warning

# Document AI field-detection fallback (Phase 4-activated — unused in Phase 3)
documentai_location: str = "us"
documentai_processor_id: str = ""        # Form Parser processor; empty in Phase 3
```

**Dependencies (`backend/pyproject.toml`):** add **`pymupdf`** (`fitz`) — the single rendering lib
for every placement path: opens **both** image and PDF uploads uniformly, fills native **AcroForm**
widgets, and inserts text at template coordinates. Replaces the earlier `reportlab` plan. `pypdf`
(Phase 1) stays for page counting; `pillow` (Phase 1) for image sizing. **No rasterizer/poppler
dependency.** *License note: PyMuPDF is AGPL — fine for this portfolio/non-distributed project;
revisit only if this ever ships as distributed commercial software.*
Add **`opencv-python-headless`** (+ its `numpy`) for the skew/rotation sanity check (§8.4.4) —
headless so no GUI/X11 libs are pulled into the server/Docker image.
**`google-cloud-documentai`** is **listed but Phase-4-activated** — not imported on any Phase-3
path. No new frontend deps.

**Auth note (Document AI, Phase 4):** Gemini is called with an **API key** via `google-genai`;
Document AI uses **GCP service-account** auth (`GOOGLE_APPLICATION_CREDENTIALS`). "Same GCP project"
holds for *billing*, but it is a different auth mechanism, not a drop-in — provisioning is a Phase-4
task.

**`.env.example`:** no new required secrets in Phase 3. Optionally note `VERIFY_LOW_CONFIDENCE`,
`RENDER_WATERMARK_TEXT`, and the (empty, Phase-4) `DOCUMENTAI_*` keys.

---

## 6. Agent pipeline changes

### 6.1 `agent/state.py` — extend `FieldResult`
Add: `verified: bool` (already declared in Phase 2's `FieldResult` — keep), `verification_method:
str | None`. Add `candidate_snippet: str | None` to the intermediate lookup dict shape (documented
in the comment). *(No `bbox`/`bbox_source` — placement is resolved deterministically at render time
from the template, not carried on state; §8.4.)*

### 6.2 `agent/graph.py` — insert the verification node
```
form_schema ──(type_mismatch?)──▶ END
     │ no
     ▼
profile_lookup ──▶ document_verification ──▶ confidence_scorer ──▶ END
```
- **`form_schema` node**: unchanged from Phase 2 (classify + resolve type + load `field_specs`). No
  box detection — field placement is a render-time concern (§8.4), not part of the fill graph.
- **`document_verification` node** (new): pure over `(state, config)`; calls
  `document_verification_tool.verify(state["fields"], verifier=cfg["verifier"])`.
- **`confidence_scorer` node**: unchanged wiring; the tool logic is extended (§6.3).

### 6.3 `confidence_scorer_tool` — fold in verification (extends Phase 2)
Per field, after verification has annotated `verified`/`verification_method`:

**Confidence:**
- missing (`value is None`) → `0.0` (unchanged).
- `verification_failed` (`verified is False` **and** value present) → `verify_low_confidence`
  (0.30, band `low`).
- `verification_method == "user"` (human correction) → `1.0`.
- `verified` **exact** → `max(inherited, ocr_confidence_high)` (promote to `high`; Decision 3).
- `verified` **semantic/llm** → inherited score (the Phase-2 candidate/user-acted value), **no**
  promotion.

**Flags** (all that apply) — `flags` gains `verification_failed`:
```
flags = {
  "missing":           None | "no_mapping" | "no_candidate",
  "verification_failed": (value is not None and verified is False),
  "high_stakes":       spec.high_stakes,
  "unverified_source": candidate.status in {needs_confirmation, failed_validation},
  "low_confidence":    confidence < confidence_threshold (0.90),
  "transformed":       transformed,          # recorded, never a reason
}
needs_review = missing or verification_failed or high_stakes or unverified_source or low_confidence
```
**`review_reason`** (single, precedence — Decision 4):
`missing` → `verification_failed` → `high_stakes` → `unverified_source` → `low_confidence`.

> Note the interaction: a high-stakes field (e.g. `date_of_birth`) that **verifies exact** still
> gets `needs_review=true` via `high_stakes` (FR8 is unconditional) — verification does not
> excuse the mandatory review; it just makes the value one the reviewer can approve with a green
> "verified against source" signal instead of a red one.

### 6.4 Vision provider — one addition (`services/ocr/vision_llm.py`)
Same Gemini provider, same `_generate_json` + `VisionExtractionError` transient/terminal handling:
- `verify_value_on_document(images: list[bytes], value: str) -> bool` — strict boolean JSON: "does
  this exact value appear on this identity document?" Used only on deterministic-miss escalation
  (§3.2).

**No `locate_fields`** — the vision-LLM is deliberately **not** asked for pixel coordinates.
Placement comes from deterministic templates (§8.4); the AI *field-detection* fallback for
template-less forms is Document AI (Phase 4), not the vision-LLM.
> Consult the project's `claude-api`/provider reference before writing/altering the Gemini calls
> (model id, image parts, structured output), per the Phase-1/2 note.

### 6.5 `fill_form_task` — extend the pipeline (`workers/tasks.py`)
1–2. As Phase 2 (load form, `processing`, fetch+preprocess the blank form → images).
2b. **Skew sanity check (§8.4.4):** call `image_quality.estimate_skew(images[0])`; if
   `abs(angle) > skew_warn_degrees`, set `Form.skew_angle` and a safe, non-PII
   `placement_warning`. **Best-effort** — any detector error is swallowed and simply leaves the
   warning unset; it never fails or retries the fill. Advisory only for the coordinate path.
3. **Snapshot now carries snippets:** `_build_profile_snapshot` decrypts
   `source_snippet_encrypted` into each `CandidateView.source_snippet` and **outer-joins** Document
   (§4.3) so manual candidates appear.
4. **Build the `verifier` closure:** `verifier(value, source_doc_id)` → fetch that document's bytes
   (memoized per `source_doc_id` for the run), preprocess, call `verify_value_on_document`. A
   `None` `source_doc_id` (manual candidate) can't be re-verified against a doc → treat as an
   automatic **fail** if it ever reaches escalation (manual candidates are user-origin; they should
   have matched deterministically or are user-acted anyway).
5. `graph.invoke(..., config={"configurable": {"snapshot", "images", "classifier", "verifier":
   verifier}})`. *(No box detector — placement is a render-time template lookup, §8.4.)*
6. `type_mismatch` → unchanged (no fields, safe `fill_error`, terminal).
7. **Persist** each `FieldResult` incl. `verified`, `verification_method` (§7). Compute terminal
   status: `approved` if **no** field is outstanding, else `in_review`. Set `filled_at`,
   `detected_form_type`; commit. Log by `form_id`/counts only.
8. **Retries:** unchanged posture — Gemini 429/5xx/timeout (classification **or** verification) +
   S3/network blips → retry with capped backoff; terminal error → `failed` + safe `fill_error`.
   Idempotent (delete-then-insert), so a retry can't duplicate fields **or** clobber review state
   (a re-run only happens from a non-terminal form — Decision 9).

---

## 7. Persisting a filled+verified field
Extends Phase 2's `_persist_form_fields` (delete-then-insert on `(form_id, field_name)`):
- filled: `value_encrypted = encrypt_field(value, aad=build_aad(form_id, field_name))`;
  `value_masked = mask_for(profile_key, value)` (Aadhaar/PAN only). **New:** `verified`,
  `verification_method`.
- missing: `value_encrypted=None`, `value_masked=None`, `verified=False`, `verification_method` null.
- `reviewed=False`, `review_action=None`, `reviewed_at=None`, `corrected_value_encrypted=None` at
  fill time (Phase 3 review sets them later).
- copy `profile_key`, provenance FKs, `confidence`, `confidence_band`, `high_stakes`,
  `transformed`, `needs_review`, `review_reason`, `flags`.

---

## 8. Review, download & rendering

### 8.1 Review lifecycle (server-authoritative)
- **Field outstanding** ⇔ `needs_review AND NOT reviewed`.
- **Form `approved`** ⇔ in the review lifecycle with **zero** outstanding fields; else `in_review`.
  A zero-flag pipeline lands directly `approved` (Decision 8).
- A review action recomputes the form status from all its fields **inside the same transaction**.
- **Post-approval edit (Decision 9):** a `correct` action on a field of an **`approved`** form
  sets that field `reviewed=false` (re-opening it) before applying the new value, so the form
  recomputes to `in_review`; the user must re-approve. **Any** successful review action clears
  `rendered_s3_key` (the cached PDF is stale).

### 8.2 `POST /api/forms/{id}/review` — per-field actions
Body (one action per call):
```jsonc
{
  "field_id": "…",
  "action": "approve" | "correct" | "approve_blank",
  "value": "12/04/1998",              // required for "correct"
  "propagate_to_profile": false        // "correct" only (Decisions 10/11)
}
```
- **`approve`**: accept the current effective value. `reviewed=true`, `review_action="approved"`,
  `reviewed_at=now`, `needs_review` unchanged in meaning (the flag stays for audit; *outstanding*
  is now false). No value change.
- **`correct`**: validate per Decision 12 (typed/high-stakes format-checked in the **form's**
  format; free-text accepted). Store `corrected_value_encrypted` (AAD `(form_id, field_name)`),
  recompute `value_masked`, set `verified=true`, `verification_method="user"`, `confidence=1.0`,
  `confidence_band="high"`, `reviewed=true`, `review_action="corrected"`, `reviewed_at=now`.
  Invalid value → `422 INVALID_VALUE`. If `propagate_to_profile`:
  - field has `profile_field_id` → update that `ProfileField` (convert form value → canonical:
    dates re-parsed to ISO, IDs normalized; reuse Phase-1 validators; `status="user_corrected"`,
    `confidence=1.0`). Canonical conversion failure → `422`.
  - field is missing but `profile_key` non-null → **synthesize** a manual `ProfileField`
    (`origin="manual"`, `source_doc_id=NULL`, `status="user_corrected"`, `confidence=1.0`,
    canonical value, masked if sensitive).
  - field `profile_key` is null (`no_mapping`) → `propagate_to_profile` ignored (422 if explicitly
    true? — **no**: silently no-op with a `warning` in the response body, since there is no
    canonical target).
- **`approve_blank`** (Decision 13): only valid on a field with no value. `reviewed=true`,
  `review_action="approved_blank"`, `reviewed_at=now`, value stays null. Field is no longer
  outstanding.

**Response:** the updated `FormFieldReviewOut` + the recomputed form `status` + a
`download_ready: bool` (`status == "approved"`).

**PII:** request `value` for a corrected Aadhaar/PAN is accepted but **never logged**; the
response `display_value` is masked for sensitive `profile_key`s (§8.6). Cross-user → `404`.

### 8.3 `GET /api/forms/{id}/review` — the review projection
`200` with the form status, `download_ready`, counts (`total`, `outstanding`), the
`placement_warning` (null when the scan looks upright; §8.4.4), and **all** fields (flagged and
not) as `FormFieldReviewOut` — each with `display_value` (masked), `confidence`, `confidence_band`,
`verified`, `verification_method`, `high_stakes`, `needs_review`, `review_reason`, `reviewed`,
`review_action`, and `source` (`profile_field_id`, `document_id`, `doc_type`) for the side-by-side.
Cross-user → `404`. (This is the review-oriented sibling of Phase 2's `GET /forms/{id}`; the latter
stays for status polling.)

### 8.4 Field placement & rendering (`services/form_renderer.py`)

Placement is **template-first and deterministic** — no AI at render time for known forms. This is
the primary correctness fix over an LLM-coordinate approach (Decision 6).

#### 8.4.1 Placement templates
The Phase-2 template JSON (`templates/{form_type}.json`) gains a `placement` block and per-field
placement (one source of truth per form type — the fill mapping and the placement live together):
```jsonc
{
  "form_type": "income_certificate",
  "display_name": "Income Certificate",
  "placement": {
    "reference_page_size": [595, 842],     // page authored against, in PDF points (A4 here)
    "default_font_size": 10
  },
  "required_fields": [
    {
      "name": "applicant_name", "profile_key": "full_name",
      "high_stakes": false, "format": "as_is",
      "placement": { "page": 1, "x": 120, "y": 340 }        // absolute coords, OR:
      // "placement": { "acro_field": "ApplicantName" }      // native fillable-PDF field name
    }
  ]
}
```
- **Coordinate convention:** PyMuPDF **top-left origin, PDF points (1/72")**, y increasing
  downward — stated so template authors stay consistent.
- **Scaling:** coordinates are authored against `reference_page_size`; the renderer scales them to
  the *actual* uploaded page/image dimensions, so a scan at a different DPI or an off-size page
  still lands correctly.
- **Per-field precedence:** a matched `acro_field` (on a fillable-PDF upload) → template `(x, y)`
  → unplaced.
- **Registry validation** (`form_schema_tool`) is extended to validate the `placement` shape
  (page ≥ 1, numeric coords or a non-empty `acro_field`, known keys) **at startup** — a bad
  placement fails fast, not mid-render.

#### 8.4.2 `render(form, fields, blank_bytes, content_type) -> bytes` (PyMuPDF `fitz`)
All values are **decrypted, full** — the user's own form legitimately carries full PII (§8.6).
1. `doc = fitz.open(stream=blank_bytes, filetype=…)` — `fitz` opens **image and PDF** uploads
   uniformly (an image becomes a single-page document). Compute the per-page scale from
   `reference_page_size` to the actual page rect.
2. Detect whether the PDF carries native **AcroForm widgets** (`page.widgets()`); build the
   placement plan from the template.
3. Per fillable field (effective value):
   - **AcroForm path** (widget named by `acro_field` exists) → set the widget's `field_value` and
     regenerate its appearance. Most robust — no pixel math, **skew-immune** (the widget carries its
     own position). **Preferred whenever available.**
   - **Coordinate path** → `page.insert_text((x·scale, y·scale), value, fontsize=…)`. **Assumes a
     reasonably flat, upright scan** — see §8.4.4 and the required code comment below.
   - **Neither** → add to the **unplaced** list.
4. Stamp the `render_watermark_text` watermark on each page.
5. Append an **"Additional fields"** page listing unplaced fields (label: value) so **nothing is
   silently dropped**. `approve_blank`/missing fields (no value) are omitted.
6. Return `doc.tobytes()` — always a single PDF (`application/pdf`).

Rendering is **deterministic** (coordinates come from the template at render time); **no LLM or
Document AI call** for template forms.

> **Required code comment (Decision 15):** the coordinate-placement branch in
> `services/form_renderer.py` MUST carry a comment stating that absolute-coordinate placement
> assumes a flat, upright scan matching the template's `reference_page_size` layout — a skewed,
> rotated, or heavily cropped upload will misplace text — and that the **AcroForm path is preferred
> when the uploaded PDF exposes named fields**. The same limitation is documented for users in
> `README.md`.

#### 8.4.4 Input-quality guard — skew/rotation sanity check (`services/image_quality.py`)
`estimate_skew(image_bytes) -> float` returns the dominant page rotation in degrees using OpenCV
(e.g. grayscale → threshold/edges → Hough lines or `minAreaRect` on the text mask → dominant
angle). Called once per fill on the first page image (§6.5 step 2b). If `abs(angle) >
skew_warn_degrees`, the task records `Form.skew_angle` + a safe `placement_warning` (e.g. *"This
scan looks rotated ~12°; coordinate-based field placement may be off — re-scan or re-photograph the
form upright for best results."*). This is a **warning, never a block**: the fill completes and the
draft is fully reviewable/downloadable. It is **best-effort** — any exception in the detector is
swallowed (the fill must not fail over a quality heuristic). It is **advisory for the coordinate
path only**; the AcroForm path is skew-immune. Rationale: absolute coordinates are authored against
an upright reference layout, so a significantly rotated scan silently misplaces values — this guard
makes that failure visible to the user *before* they rely on the output, rather than after.

#### 8.4.3 Field-detection fallback for template-less forms — **interface only (Phase 4)**
`services/form_placement/document_ai.py` — `detect_fields(images) -> list[DetectedField{name,
bbox, confidence}]` via **Google Document AI Form Parser** (purpose-built for form key-value +
bounding-box detection; far more reliable than a vision-LLM at pixel coordinates). **Phase 3
defines the interface + config only** and the function raises a clear "no template — schema
inference is Phase 4" error. It is **unreachable in Phase 3**: `POST /forms/upload` `422`s any
form type not in the template registry, so every Phase-3 form has a placement template. Phase 4
(schema inference for unseen forms) wires the real call, caches detected boxes, and routes
low-confidence/undetected fields to the same appended "unplaced fields" page (§8.4.2 step 5).

### 8.5 `GET /api/forms/{id}/download`
- `409 REVIEW_INCOMPLETE` unless `status == "approved"` (the **download gate** — FR8/UC4). This is
  the hard, non-optional block.
- If `rendered_s3_key` is set → stream it. Else render (§8.4), `put_document` to S3, set
  `rendered_s3_key`, commit, stream. `Content-Disposition: attachment; filename="<form_type>.pdf"`.
- Bearer-authenticated (like `GET /documents/{id}/file`); the SPA fetches a Blob. Cross-user →
  `404`. **Never** submits anywhere (FR7) — it only returns bytes to the authenticated owner.

### 8.6 `GET /api/forms/{id}/file`
Serve the **blank** uploaded form bytes (from `Form.s3_key`) for the review page's side-by-side
preview + overlay context. Bearer-auth, blob fetch (mirrors `GET /documents/{id}/file`). Cross-user
→ `404`.

> **Masking boundary (important):** API responses, the review UI, and logs show **masked**
> Aadhaar/PAN. The **downloaded PDF** and the **overlay** contain the **full** value — that is the
> user's own government form, the entire point of the product. The full value exists encrypted at
> rest + in-memory during render + in the delivered PDF to the authenticated owner, and **nowhere
> else** (never logged).

---

## 9. Frontend (`frontend/src`)

Builds on Phase 0's auth/shell, Phase 1's `ConfidenceField` + authed-blob pattern, Phase 2's
`FormFill` poll.

- **`pages/Review.tsx`** (route `/forms/:id/review`): loads `GET /forms/{id}/review`; renders each
  field via a review-capable `ConfidenceField` — **green** (verified / high), **yellow** (low-conf
  / semantic-verified), **red** (missing / high-stakes-unresolved / `verification_failed`), a
  **"verified against source"** badge when `verified`, and the `review_reason`. Per field:
  **Approve**, **Edit** (→ correct, with a **"also save to my profile"** checkbox →
  `propagate_to_profile`), and **Approve as blank** (missing fields only). Side-by-side **source
  document** (authed blob from `GET /documents/{id}/file`) and/or the **blank form** (`GET
  /forms/{id}/file`).
- **Placement-warning banner:** when `placement_warning` is present, show a dismissible advisory
  ("this scan looks rotated — field placement may be off; re-scan upright for best results") so the
  user understands the coordinate-based overlay may be misaligned before they rely on the download.
- **Download button:** **disabled until `download_ready`** (`status === "approved"`); a visible
  banner states review is required and the output is a draft for manual submission, never
  submitted. On click → authed blob from `GET /forms/{id}/download` → save.
- **`ConfidenceField`** is generalized here (Phase 2 deliberately used a local read-only
  `DraftField`; Phase 3 is where the shared component's approve/correct controls are used — see
  `memory/phase2-decisions.md`). Extend its props for `onApprove`/`onCorrect(value,
  propagate)`/`onApproveBlank`/`onViewSource`, `verified`, `verification_method`.
- **`api/client.ts`:** add `getFormReview(id)`, `submitReview(id, body)`, `downloadForm(id)`
  (blob), `getFormFile(id)` (blob).
- **`types.ts`:** `FormFieldReviewOut`, `ReviewAction`, extend `FormStatus` with
  `in_review`/`approved` (remove `filled` usage), update `FormFill`'s `TERMINAL_STATUSES` to
  `in_review`/`approved`/`failed`/`type_mismatch` and route to Review on success.
- **Routing/nav:** link `FormFill` success → `Review`; add the review route to the protected shell.

---

## 10. Security & edge cases (must-handle)
- **Download gate is mandatory & server-enforced** (FR8/UC4): `409` unless `approved`; a
  client-side disabled button is **not** the control — the API blocks it.
- **No auto-submit** (FR7): the only outbound artifact is a Blob to the authenticated owner; no
  submit path exists or is ever built.
- **No raw PII in logs** (CLAUDE.md): never log field values, corrected values, snippets,
  Aadhaar/PAN, images, boxes' contents, or model output. Log by `form_id`/`form_field_id`/counts.
  `fill_error` stays a fixed safe string.
- **Field-level encryption** (FR2): auto-filled **and** corrected values are AES-256-GCM with AAD
  `(form_id, field_name)`. Manual write-back candidates are encrypted with AAD `(profile_id,
  field_name)` (Phase-1 convention).
- **Masking vs. the owner's own PDF** (§8.6): masked everywhere except the encrypted store,
  in-memory render, and the delivered PDF.
- **Ownership scoping:** every form/field read + review action + download filters by `user_id`;
  cross-user → `404`.
- **Verification never launders trust:** a transient verification error retries the job; it never
  becomes "verified." A deterministic pass is preferred; the LLM is a bounded fallback.
- **Idempotent, non-clobbering re-run** (Decision 9): the pipeline runs once; a retry re-derives
  identically and can't duplicate fields or overwrite user review state (a terminal form is never
  re-filled).
- **Manual write-back constraints** (Decision 11): only when a canonical `profile_key` exists;
  `no_mapping` corrections stay form-local.
- **Corrected high-stakes still audited:** a corrected Aadhaar/DOB is `verified/user` and no longer
  outstanding, but its `high_stakes`/`flags` history is preserved for auditability.
- **Skew guard is advisory, never a gate** (Decision 15): a skewed scan still fills, reviews, and
  downloads — the `placement_warning` only informs the user that coordinate placement may be off.
  The AcroForm path is skew-immune. The detector is best-effort: its failure never fails the fill.
- **Third-party processing:** the blank form image is sent to Gemini for form classification, and
  (on verification escalation) the source ID-doc image for value verification — same disclosure
  posture as Phases 1–2. The skew check is **local** (OpenCV, no network). Nothing logged locally.
- **Deferred hardening (noted, not built):** per-user rate limiting, malware scan of uploads,
  S3 SSE-KMS, consent audit trail, deskew/auto-rotate correction (Phase 3 only *warns*; it does not
  auto-correct rotation).

---

## 11. File-by-file change list

**Backend — implement (currently stubs):**
`agent/tools/document_verification_tool.py` (`verify` + `deterministic_match`),
`services/form_renderer.py` (`render`, PyMuPDF template-first placement; **required code comment on
the coordinate-path skew assumption + AcroForm-preferred**, §8.4.2).

**Backend — edit:**
`agent/graph.py` (insert `document_verification` node; `form_schema` unchanged — no box detection),
`agent/state.py` (extend `FieldResult` with `verification_method` / lookup-dict `candidate_snippet`
comment),
`agent/tools/profile_lookup_tool.py` (carry `candidate_snippet`),
`agent/tools/confidence_scorer_tool.py` (fold in verification: confidence promotion/demotion,
`verification_failed` flag + precedence),
`agent/tools/form_schema_tool.py` (validate the template `placement` shape at registry load; expose
placement to the renderer),
`services/ocr/vision_llm.py` (add `verify_value_on_document` only — **no** `locate_fields`),
`workers/tasks.py` (`fill_form_task`: snippet snapshot + outer join, inject `verifier`, persist
`verified`/`verification_method`, `in_review`/`approved` status),
`models/form.py` (Form `rendered_s3_key`, `skew_angle`, `placement_warning`; FormField `verified`,
`verification_method`, `corrected_value_encrypted` + `effective_value_encrypted` property,
`review_action`, `reviewed_at`),
`models/profile.py` (`source_doc_id` nullable, add `origin`),
`api/routes/forms.py` (add `GET /review`, `POST /review`, `GET /download`, `GET /file`; retire
`filled` in the status projection),
`api/routes/profile.py` (`_get_source_document` guard for manual candidates),
`schemas/form.py` (`FormFieldReviewOut`, `FormReviewOut` incl. `placement_warning`,
`ReviewActionRequest`, download/file contracts),
`config.py` (`verify_low_confidence`, `render_watermark_text`, `skew_warn_degrees`, Phase-4
`documentai_*`),
`pyproject.toml` (add `pymupdf`, `opencv-python-headless`; list `google-cloud-documentai` as
Phase-4-activated),
`templates/income_certificate.json` + `templates/scholarship_application.json` (add the `placement`
block + per-field coordinates/`acro_field`).

**Backend — new:**
`db/migrations/versions/0004_verification_review.py`,
`services/image_quality.py` (`estimate_skew` via OpenCV; §8.4.4),
`services/form_placement/document_ai.py` (fallback `detect_fields` **interface + stub**; real
Document AI Form Parser integration is Phase 4).

**Docs — edit:**
`README.md` — document the coordinate-placement limitation (assumes a flat, upright scan; skewed
scans are warned about, not auto-corrected) and that AcroForm-based filling is the preferred path
when the form exposes native fields (Decision 15).

**Frontend — new/implement:**
`pages/Review.tsx`, route wiring in `App.tsx`, `api/client.ts` (add methods), `types.ts` (review
types + status change), generalize `components/ConfidenceField.tsx`, update `pages/FormFill.tsx`
terminal statuses + success routing.

**Untouched this phase (stay stubs):** `agent/tools/form_schema_tool.py` inference branch
(Phase 4), `services/ocr/tesseract.py`, `metrics/` (Phase 6), the history router + `DELETE
/api/profile` (Phase 5).

---

## 12. Testing (`backend/tests`, `frontend`)

**Backend (pytest):**
- **`document_verification_tool` (pure, stubbed verifier):** `exact` snippet match → verified/exact
  (no verifier call); a **swapped-format date** (`%m/%d` vs snippet) → deterministic `miss` →
  escalates → verifier `False` → `verification_failed`; a date that parses equal but differs in
  string form → `semantic` (no verifier call); normalized Aadhaar/PAN semantic match; free-text
  `single_line`/`upper` still `exact`; empty snippet → escalates; a verifier `True` on escalation →
  verified/llm.
- **`confidence_scorer_tool` (pure):** exact-verified promotes to high; semantic/llm keeps
  inherited; `verification_failed` → `verify_low_confidence` + top-precedence reason; the full
  precedence table incl. `verification_failed` above `high_stakes`; high-stakes verified-exact is
  **still** `needs_review`; `transformed` never a reason.
- **graph (`build_graph`) with fakes:** stub `classifier`/`verifier`/snapshot — happy path yields
  verified fields; `type_mismatch` skips verification; a verifier raising transient
  `VisionExtractionError` surfaces to the task.
- **`fill_form_task`:** persists `verified`/`verification_method`; status is `in_review` with a
  flagged field and `approved` when none; snippet snapshot decrypts + outer-join includes a manual
  candidate; transient verification error retries; re-run is idempotent and does **not** clobber
  review state (only runs from non-terminal).
- **review endpoints:** `approve`/`correct`/`approve_blank` set `reviewed` + recompute form status;
  resolving the last outstanding field → `approved` + `download_ready`; `correct` validation `422`
  on a bad DOB/Aadhaar; `propagate_to_profile` updates an existing candidate; a **missing** field
  correction with propagate **synthesizes** a manual `ProfileField` (`origin="manual"`,
  null `source_doc_id`); `no_mapping` correction ignores propagate; cross-user → `404`; corrected
  Aadhaar never returned/logged in full; post-approval `correct` re-opens the field → `in_review` +
  clears `rendered_s3_key`.
- **download:** `409` until `approved`; first download renders + persists `rendered_s3_key` +
  streams `application/pdf`; second download reuses the cache; the rendered PDF (fixture inspection)
  contains the **full** value, not masked; a post-approval edit invalidates the cache.
- **`form_renderer` (PyMuPDF):** image upload → single-page PDF with the image + text at template
  coordinates + watermark; **AcroForm** PDF upload → named widgets filled; coordinate-only PDF
  upload → text at scaled coords; a field with no coord/`acro_field` lands on the appended
  "Additional fields" page; `approve_blank`/missing fields omitted; coordinate scaling holds when
  the uploaded page size differs from `reference_page_size`.
- **placement templates:** registry validation accepts a valid `placement` block and **rejects** a
  bad one (page < 1, non-numeric coord, empty `acro_field`, unknown key) at load time.
- **fallback stub:** `services/form_placement/document_ai.detect_fields` raises the "Phase 4" error
  and is never reached (upload `422`s an unknown `form_type`).
- **skew guard (`image_quality.estimate_skew`):** a synthetically rotated fixture (> threshold)
  yields a `placement_warning` on the `Form` (surfaced in `GET /review`); an upright fixture yields
  none; a detector exception is swallowed and the fill still completes (best-effort, never fatal).
- **migration:** `0004` applies; `forms` gains `rendered_s3_key`/`skew_angle`/`placement_warning`;
  `profile_fields.source_doc_id` is nullable + `origin` defaults `"document"`; existing rows
  back-fill `origin="document"`.

**Frontend (vitest, light):** Review renders correct green/yellow/red bands + the verified badge;
approve/correct(+propagate)/approve-blank call the API and update state; the download button is
disabled until `approved` and enabled after; sensitive values render masked; source-doc
side-by-side fetches via the authed blob path.

---

## 13. Metrics seam (full instrumentation is Phase 6)
Phase 3 makes these **recoverable**, not dashboarded: **verification pass/fail rate** (`verified` +
`verification_method` distribution across `FormField`), an **auto-fill accuracy proxy** (fields
approved-as-is vs. corrected — a correction is a "the auto-fill was wrong" signal), and
**review time** (`reviewed_at − filled_at` per field; `approved` transition − `filled_at` per
form). No `metrics/` code this phase.

---

## 14. Acceptance checklist (Done-When, enumerated)
1. Upload a known blank form → pipeline runs `pending → processing`, verification re-checks every
   fillable field against its source doc, and lands **`in_review`** (flags present) or **`approved`**
   (none) — `filled` is no longer produced.
2. `GET /forms/{id}/review` lists every field with `verified`, `verification_method`,
   `confidence`/band, `high_stakes`, `needs_review`/`review_reason`, `reviewed`, and `source`
   provenance for side-by-side.
3. A field whose formatted value **exactly** matches its source snippet is `verified` (method
   `exact`) and promoted to high; a **swapped date-format** value fails verification →
   `verification_failed` (top precedence) at low confidence; high-stakes fields (`date_of_birth`,
   `aadhaar_number`) are **still** flagged even when verified.
4. `GET /forms/{id}/download` returns **`409`** while any flagged field is outstanding.
5. Per-field **approve** / **edit(correct)** / **approve-as-blank** each resolve a field; a
   corrected DOB/Aadhaar is format-validated (`422` on bad input); a human correction becomes
   `verified/user`, confidence 1.0.
6. Opting **"also save to my profile"** on a correction updates the source candidate; the same on a
   **hand-typed missing field** creates a **manual** profile candidate (`origin="manual"`) with no
   source document; a `no_mapping` field cannot write back.
7. When the **last outstanding field** is resolved, the form auto-transitions to **`approved`** and
   `download_ready` is true.
8. `GET /download` then renders the approved values onto the original form via **deterministic
   template placement** (AcroForm widget fill when the PDF has named fields, else template
   coordinates — **no AI call**) → a PDF (with a "DRAFT — NOT SUBMITTED" watermark), persists it to
   S3 (`rendered_s3_key`), and streams it; a second download reuses the cached PDF; **nothing is
   ever submitted** (FR7).
9. The downloaded PDF/overlay contains the **full** Aadhaar/DOB (owner's own form), while every API
   response, the review UI, and all logs show only the **masked** form.
10. **Correcting** a field on an `approved` form re-opens it → back to `in_review`, download
    re-locks (`409`), and the cached PDF is invalidated (regenerated on next download).
11. A field with **no template coordinate / AcroForm mapping** appears on an appended "Additional
    fields" page in the PDF (never silently dropped); an approved-blank field is omitted.
11b. A significantly **rotated/skewed** upload surfaces a non-blocking `placement_warning` in `GET
    /review` (and a banner in the review UI) telling the user to re-scan; the fill still completes
    and the form is still downloadable. An upright scan produces no warning. The coordinate-path
    skew assumption is documented in `README.md` and in a code comment by the coordinate-filling
    logic; AcroForm filling is noted as the preferred, skew-immune path.
12. At-rest: `form_fields.value_encrypted`/`corrected_value_encrypted` are ciphertext; a manual
    profile candidate is encrypted; grep of api+worker logs for the test PII → zero matches.
13. The review UI blocks download until complete, shows green/yellow/red bands + a "verified against
    source" badge, and offers per-field approve/edit/approve-blank with source side-by-side.
14. `ruff`/`mypy` clean; new backend tests + light frontend tests green (per
    `memory/dev-environment`).
```
