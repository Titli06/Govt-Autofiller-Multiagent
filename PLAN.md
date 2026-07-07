# Build Plan — GovForm Auto-Filler

Phases are **vertical slices**: each one cuts through DB → backend → frontend to ship one
complete, demoable feature, rather than building a whole layer at a time. Do them in order —
each phase assumes the previous one works. Every phase ends with a "Done when" you can actually
click through.

Maps to PRD §11 (build order) and the use cases (UC*)/requirements (FR*) in
[govform-autofiller-prd.md](govform-autofiller-prd.md).

---

## Phase 0 — Walking skeleton: auth + app shell  ✅ implemented (see [SPEC.md](SPEC.md))
Foundational slice so every later feature has a real user to hang data off of. No product
feature yet, but end-to-end through all three layers.

- [x] **DB:** `User` + `RefreshToken` + `EmailVerificationToken` models; first Alembic migration (`0001_initial_auth`); sync DB session wiring (`db/base_class.py` Base + `db/base.py` aggregator)
- [x] **Backend:** `core/security.py` (bcrypt + JWT access tokens + opaque-token hashing); `POST /api/auth/{register, verify-email, resend-verification, login, refresh, logout}` + `GET /api/auth/me`; `get_current_user` dependency. Scope grew per interview: **mandatory email verification** (Mailpit), **DB-backed rotating refresh tokens** with family reuse-detection, enumeration-safe login/resend
- [x] **Frontend:** login/register/verify pages; **in-memory access token + httpOnly refresh cookie** with silent-refresh-on-load (`api/client.ts`, `auth/AuthContext.tsx`); protected app shell routing Upload/Review/History behind `ProtectedRoute`
- [x] **Infra (files written, not yet run here):** `docker-compose.yml` adds **Mailpit**, Postgres/Redis healthchecks, health-gated `depends_on`, and a migrate-on-start entrypoint; backend/frontend Dockerfiles; split `/health` (liveness) + `/health/ready` (DB+Redis)
- [x] **Verified without Docker:** backend **28 pytest** green, `ruff` clean, migration applies, `app.main` imports under uvicorn path; frontend **4 vitest** green, `tsc --noEmit` + ESLint clean; `docker-compose.yml` parses (7 services)
- [x] **Verified live stack (Docker now installed):** `docker compose up --build` brings up all 7 services healthy (`postgres`, `redis`, `minio`, `mailpit`, `api`, `worker`, `frontend`); `/health` → `{"status":"ok"}`, `/health/ready` → `{"status":"ready","checks":{"postgres":"ok","redis":"ok"}}`; fixed a real bug found in the process — `email-validator` (needed by pydantic `EmailStr`) was installed ad hoc on the host during earlier dev but missing from `backend/pyproject.toml`, so the container image 500'd on startup until added as a real dependency
- [x] **Done when:** a user can register, log in, and land on an (empty) authed dashboard
      → verified end-to-end through the frontend's own request path (`:5173` → Vite proxy → `api:8000`): register → Mailpit (`:8025`) delivered the verification email → `verify-email` → `login` (access token + httpOnly refresh cookie) → `/api/auth/me` returns the authed user through the same proxy path the SPA uses.

---

## Phase 1 — Profile ingestion from ID documents (UC1, FR1/FR2/FR12)  ✅ implemented (see [SPEC-PHASE1.md](SPEC-PHASE1.md))
Upload an ID doc → vision-LLM extracts it → encrypted profile you can see. First real feature.

- [x] **DB:** `Document` model (metadata, `s3_key`, `ocr_status`); `Profile` + `ProfileField` (multi-candidate, field-level **AES-256-GCM encrypted** values + `source_doc_id` provenance); Alembic `0002_profile_ingestion`
- [x] **Backend:** `services/storage.py` (S3/MinIO put/get/delete, moto-tested); `core/encryption.py` (field-level PII encrypt/decrypt + Aadhaar/PAN masking) + `core/validators.py` (PAN regex, Aadhaar Verhoeff, DOB parse, snippet grounding); `services/preprocessing.py` (HEIC/PDF/orientation); `services/ocr/vision_llm.py` (**Google Gemini** structured-JSON extraction — swapped from the originally-built Anthropic version, same interface, see memory) + `services/extraction.py` (deterministic confidence grounding — never LLM self-report alone); `ocr_extract_task` (bounded retry, partial/failed/type_mismatch states, idempotent); `POST /api/documents/upload`, `GET /api/documents/{id}/status`, `GET /api/documents/{id}/file`, `GET /api/profile`, `POST /api/profile/fields/{id}/{confirm,correct}`
- [x] **Frontend:** Upload page (doc-type select, camera capture, status polling), Profile page (grouped fields, confirm/edit, source-doc preview), `ConfidenceField` component (green/yellow/red bands)
- [x] **Verified without Docker:** backend **129 pytest** green (encryption, validators, models, storage via moto, preprocessing, extraction/grounding rubric, OCR task incl. retry/idempotency, document + profile API routes, worker import-graph regression), `ruff` clean, `mypy` clean (aside from 2 pre-existing Phase 0 findings, untouched); frontend **22 vitest** green, `tsc --noEmit` + ESLint clean.
- [x] **Verified live stack (2026-07-06):** `docker compose up --build` — all 7 services healthy; migration `0002_profile_ingestion` applied against real Postgres. Found and fixed a real bug in the process: the **worker process crashed with `NoReferencedTableError`** on `Document.user_id`'s FK, because `celery_app.py`'s `include=["app.workers.tasks"]` never pulled in `app.models.user` — masked by every unit test because `conftest.py` imports the full model aggregator directly. Fixed by importing `app.db.base` in `celery_app.py`; added a subprocess-based regression test (`test_worker_import_graph.py`) that reproduces the exact worker import graph and fails without the fix. After the fix: registered → verified (Mailpit) → logged in → uploaded a synthetic Aadhaar image → **real Gemini API call succeeded** → extracted `full_name`/`gender`/`address` at high confidence (auto-confirmed) and `dob`/`aadhaar_number` at medium confidence (high-stakes → `needs_confirmation`, DOB correctly normalized DD/MM/YYYY→ISO); confirmed a field, corrected another (with 422 on an invalid value), streamed the source doc back, and confirmed a mismatched doc-type upload → `type_mismatch` with zero fields written. Spot-checked Postgres directly: `profile_fields.value_encrypted` is ciphertext, not plaintext; grepped worker+api container logs for the test PII (name/Aadhaar digits/address/DOB) — zero matches.
- [x] **Done when:** upload an Aadhaar/PAN image → poll → see extracted, encrypted-at-rest profile fields with source-doc provenance
      → verified end-to-end against the live stack with a real Gemini extraction, per the line above.

---

## Phase 2 — Known-template form fill (UC2, FR3/FR5/FR6)  ✅ implemented (see [SPEC-PHASE2.md](SPEC-PHASE2.md))
Upload a form the system has a template for → agent maps profile data → draft with per-field
confidence. Auto-fill path only; review UI comes in Phase 3.

- [x] **DB:** `Form` + `FormField` (**encrypted** value via `core/encryption.py` from Phase 1, `profile_field_id`/`source_doc_id` FKs `ON DELETE SET NULL`, `confidence`, `needs_review`/`review_reason`, `reviewed` placeholder, `flags` audit JSON); Alembic `0003_forms`; `templates/income_certificate.json` (+ `format` per field) and new `templates/scholarship_application.json`
- [x] **Backend:** LangGraph `agent/graph.py` wiring `form_schema_tool` (known-template registry + vision-confirm mismatch check) → `profile_lookup_tool` (deterministic `profile_key` mapping, user-acted/confidence/recency candidate selection, format transforms) → `confidence_scorer_tool` (provisional score inherited from the profile candidate — **not** a fresh cross-check of the mapped/formatted value; that's Phase 3 — plus computed, not-yet-enforced `needs_review`/`review_reason`); `services/ocr/vision_llm.classify_form`; `fill_form_task` (bounded retry, idempotent, always terminal); `POST /api/forms/upload`, `GET /api/forms/{id}`
- [x] **Frontend:** `pages/FormFill.tsx` — form-type select + drag-drop/camera upload + status polling + a **read-only** draft view (confidence bands, high-stakes/review badges); no approve/edit/download yet
- [x] **Verified without Docker:** backend **211 pytest** green (template registry validation, pure `profile_lookup_tool`/`confidence_scorer_tool` unit tests incl. the full review-reason precedence table, `build_graph()` end-to-end with fakes incl. type_mismatch/unknown-classification paths, `fill_form_task` incl. retry/idempotency, forms API incl. cross-user 404 + masked-Aadhaar-never-in-response), `ruff` clean, `mypy` clean (aside from the 2 pre-existing Phase 0 findings, untouched); frontend **27 vitest** green, `tsc --noEmit` + ESLint clean.
- [x] **Verified live stack (2026-07-06):** `docker compose up --build` — all 7 services healthy; migration `0003_forms` applied against real Postgres (`forms`/`form_fields` tables present); worker registered both tasks (`fill_form_task`, `ocr_extract_task`) with no import errors. Registered → verified (Mailpit) → logged in → uploaded a synthetic Aadhaar image → real Gemini extraction succeeded (as in Phase 1) → confirmed `dob` and `aadhaar_number` → uploaded a synthetic blank income-certificate form → **real Gemini `classify_form` call** correctly detected `income_certificate`, matching the declared type → draft `filled` in ~5s with every Decision from the interview visibly correct: `applicant_name`/`address` mapped and not flagged (confirmed, high confidence, non-high-stakes); `date_of_birth` reformatted `1995-08-15`→`15/08/1995` (`transformed: true`) via the user-confirmed candidate → **confidence 1.0**, yet still `needs_review: true, review_reason: "high_stakes"`; `aadhaar_number` likewise confidence 1.0 but flagged high-stakes and displayed masked (`XXXX XXXX 2346`); `father_name` (mapped, no PAN ever uploaded) → `no_candidate`; `annual_income` (`profile_key: null`) → `no_mapping`. Re-uploaded the same form declared as `scholarship_application` → Gemini confidently detected `income_certificate` → `type_mismatch`, zero `form_fields` written. Verified the Vite dev proxy (`:5173/api/...`) reaches the same live API. Spot-checked Postgres directly: `form_fields.value_encrypted` is ciphertext for every filled field, `NULL` for unfillable ones; grepped `api`+`worker` container logs for the test PII (name, Aadhaar digits, DOB, address) — zero matches; only `form_id`/`status`/`user_id` (UUID) appear in log lines.
- [x] **Done when:** upload a known form type → agent produces a draft where each field shows a provisional confidence score sourced from profile data
      → verified end-to-end against the live stack with real Gemini calls for both extraction and form classification, per the line above.

---

## Phase 3 — Verification + HITL review + download (UC4/UC7, FR7/FR8/FR9)  ✅ implemented (see [SPEC-PHASE3.md](SPEC-PHASE3.md))
Close the trust loop: verify against source docs, force human review of flagged fields, gate
download. This is the safety-critical slice.

- [x] **DB:** `FormField` review/verification columns (`verified`, `verification_method`, `corrected_value_encrypted`, `review_action`, `reviewed_at`; `reviewed` now written); `Form` gains `rendered_s3_key` + skew guard (`skew_angle`, `placement_warning`); status lifecycle **`pending → processing → (in_review | approved | failed | type_mismatch)`** — `filled` retired; `profile_fields.source_doc_id` relaxed to nullable + `origin` (`document`|`manual`) so a review correction can write back a manual candidate; migration `0004`
- [x] **Backend:** `document_verification_tool` — inserted into `agent/graph.py` **between** `profile_lookup_tool` and `confidence_scorer_tool`; **hybrid** re-check of each *mapped/formatted* value against its source doc — deterministic snippet re-ground first (catches e.g. a date-format conversion mistake), vision-LLM escalation only on a miss — folding into a final score (`confidence_scorer_tool` promotes verified-exact, drops `verification_failed` to a new top-precedence flag; **high-stakes always reviews**, FR8); `GET/POST /api/forms/{id}/review` (per-field approve / correct[+optional profile write-back] / approve-blank); `services/form_renderer.py` overlays approved values onto the original form → **PDF via deterministic template placement** (AcroForm-first, else template coordinates; PyMuPDF) + persists to S3 (referenced from `Form` for Phase 5 re-download) + `GET /api/forms/{id}/download` — **blocked until all flagged fields resolved**; **OpenCV skew/rotation sanity check** on upload → non-blocking `placement_warning` (coordinate placement assumes a flat, upright scan); Document AI field-detection fallback for template-less forms is **interface-only (real integration Phase 4)**; verify **never auto-submits**
- [x] **Frontend:** Review page — confidence-coded fields (green verified / yellow low-conf / red missing or high-stakes / verification-failed), one-click approve/edit/approve-blank each, side-by-side with source doc, optional "also save to my profile" per correction, a **skew/placement warning banner**; download button disabled until review complete
- [x] **Docs:** `README.md` documents the coordinate-placement limitation (flat, upright scan assumed; skew is warned, not auto-corrected) and that AcroForm-based filling is the preferred, skew-immune path
- [x] **Done when:** a form with flagged fields cannot be downloaded until the user approves/corrects each; approved output downloads as a filled PDF overlaid on the original (template placement), never submitted; a significantly skewed upload warns the user to re-scan rather than silently misplacing fields
      → verified end-to-end against the live stack with real Gemini calls (`classify_form` + `verify_value_on_document`), a real `0004` Postgres migration, and real MinIO storage: exact-match verification with zero LLM calls on the happy path, high-stakes-always-reviews even when verified, type-mismatch detection, the skew guard firing on a genuinely rotated (~14°) fixture and staying silent on an upright one, manual-candidate synthesis + propagation-to-existing-candidate, the download 409 gate, cross-user 404s, the post-approval-edit reopen/re-lock/re-render mechanic, full unmasked values in the downloaded PDF vs. masked everywhere else, encryption-at-rest, and a zero-PII log sweep across the whole session. Found and fixed one real bug live (a Unicode em-dash in the watermark default silently corrupted on render — see `memory/phase3-decisions.md`). Browser walkthrough of the Review UI itself was not done (no headless-browser tool available); vitest component/page tests stand in for it.

---

## Phase 4 — Schema inference for unseen forms (UC3, FR4/FR5)
The hardest, most differentiating path (PRD's core thesis: *semantic* field matching on
unstructured paperwork with no fixed schema, not string matching) — do it only after the
known-template path is solid. Phase 3 already scaffolded the two things this phase actually
finishes; both are currently interface-only and provably unreachable.

- [ ] **DB:** `Form.schema_source` (`template` | `inferred`) for auditability/metrics (PRD §9's
      schema-inference-success-rate metric, Phase 6, reads this)
- [ ] **Backend — field detection (finishes the Phase 3 stub):** `services/form_placement/document_ai.py`'s
      `detect_fields()` currently raises a fixed "Phase 4" error — wire it to the real **Google
      Document AI Form Parser** call (purpose-built bounding-box detection; the vision-LLM is
      deliberately never asked for pixel coordinates, per `services/ocr/vision_llm.py`'s
      docstring). Needs its own credential provisioning: Document AI uses GCP
      **service-account** auth (`GOOGLE_APPLICATION_CREDENTIALS`), not the **API-key** auth
      Gemini uses via `google-genai` — same GCP project only means shared billing, not a
      drop-in. Config keys (`documentai_location`, `documentai_processor_id`) and the
      `google-cloud-documentai` dependency are already in place from Phase 3, just unused.
- [ ] **Backend — reachability gate (finishes the Phase 3 stub):** `POST /api/forms/upload`
      currently `422`s (`UNKNOWN_FORM_TYPE`) any `form_type` not in the template registry —
      by design, this makes the inference path provably unreachable today. Phase 4 must relax
      this gate (e.g. accept a declared type with no template as "infer it") before any of the
      above can ever run; needs its own regression test, since nothing exercises this path yet.
- [ ] **Backend — semantic field-to-profile mapping (the actual hard part, net-new code):**
      NOT an extension of `agent/tools/profile_lookup_tool.py` — that tool only does exact,
      human-pre-declared `profile_key` lookup from a template JSON, with zero semantic
      capability. An inferred form has no human-authored mapping, so this needs a new agent
      tool that semantically matches each Document-AI-detected field *label* (e.g. "Father's
      Name" vs "Name of Father") to one of `form_schema_tool.CANONICAL_PROFILE_KEYS`, LLM-based
      per PRD §5's explicit rejection of regex/string matching ("brittle across format
      variance"). The match confidence caps the field's score in `confidence_scorer_tool`,
      consistent with "inferred fields default to lower confidence" (CLAUDE.md/PRD).
- [ ] **Backend — high-stakes policy for inferred fields:** templates declare `high_stakes` by
      hand per field; an inferred field has no such declaration. Derive it instead from the
      *matched canonical profile_key* (dob/aadhaar_number/pan_number are always high-stakes
      regardless of source) rather than per-field metadata that doesn't exist yet.
- [ ] **Backend — rendering:** `services/form_renderer.py` gains a placement source for
      `schema_source == "inferred"` forms — Document-AI-detected bounding boxes instead of a
      template's `(x, y)` — reusing the existing "Additional fields" appended-page fallback
      (Phase 3, §8.4.2) unchanged for anything undetected/low-confidence.
- [ ] **Backend — everything else in the Phase 3 pipeline is reused unchanged:**
      `document_verification_tool`, `confidence_scorer_tool`, and the review/approve/download
      endpoints don't care whether a field's mapping/placement came from a template or
      inference — no HITL/download-gate logic to rebuild here.
- [ ] **Frontend:** surface "inferred form" state — reuse the Phase 3 placement-warning banner
      UI pattern rather than building a new one. The Review page itself needs no new logic;
      it's already generic over any `FormFieldReviewOut`.
- [ ] **Testing:** mock Document AI calls the same way Gemini's `classify_form`/
      `verify_value_on_document` are mocked (`app.workers.tasks`-style patching) — real
      network/billed calls are never exercised in CI.
- [ ] **Done when:** upload a form with no template → system detects its fields (Document AI),
      semantically maps what it can to profile data at a discounted confidence, runs it through
      the *same* verification/review/download pipeline as a known-template form, fills what it
      can, and routes the rest to review.

---

## Phase 5 — History + data deletion (UC5/UC6, FR10/FR11)
Reuse profile across forms and honor the data-minimization commitment.

- [ ] **DB:** cascade-delete rules (profile + documents + form history) for full purge
- [ ] **Backend:** `GET /api/history` (past filled forms); `DELETE /api/profile` cascade (also purge S3 objects); confirm profile is fetched once and reused across forms in a session
- [ ] **Frontend:** History dashboard (past forms + profile data); explicit, easy-to-find delete-my-data flow with confirmation
- [ ] **Done when:** a user can file multiple forms reusing one profile, view them in history, and permanently delete everything in one action

---

## Phase 6 — Metrics instrumentation (PRD §9, NFR)
Cross-cutting slice: the metrics are part of the deliverable, not optional telemetry.

- [ ] **DB:** persist per-run metrics (latency, auto-fill %, schema-inference outcome)
- [ ] **Backend:** `metrics/instrumentation.py` timers/counters across pipeline stages — end-to-end latency, % auto-filled at high confidence, auto-fill accuracy vs. ground truth (test set), time-saved, schema-inference success rate
- [ ] **Frontend:** lightweight metrics view (per-form: fields auto-filled vs. reviewed, latency)
- [ ] **Done when:** each completed form reports its latency and auto-fill/review breakdown, and aggregate metrics are queryable

---

### Notes carried from CLAUDE.md (don't violate)
- Never build an auto-submit feature. Output is always a user-downloaded draft.
- Confidence must ultimately be grounded in **exact source-document match** (via `document_verification_tool`, Phase 3+), never LLM self-report alone. Phase 2's pre-verification score is explicitly provisional, not a final trust signal.
- High-stakes fields (money, legal declarations, non-exact date/ID) always route to review.
- PII encrypted field-level at rest; never log raw PII; deletion is first-class.
- Every auto-filled field must trace back to its source doc + confidence score.