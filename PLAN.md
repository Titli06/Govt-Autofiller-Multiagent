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

## Phase 3 — Verification + HITL review + download (UC4/UC7, FR7/FR8/FR9)
Close the trust loop: verify against source docs, force human review of flagged fields, gate
download. This is the safety-critical slice.

- [ ] **DB:** review-state transitions on `FormField` (per-field `reviewed`, `review_reason`); form status lifecycle (draft → in_review → approved)
- [ ] **Backend:** `document_verification_tool` — inserted into `agent/graph.py` **between** `profile_lookup_tool` and `confidence_scorer_tool`, re-checking each *mapped/formatted* form value against the source document (catches errors the Phase 1 extraction confidence alone wouldn't, e.g. a date-format conversion mistake) and updating the provisional score into a final one; HITL branch (below `CONFIDENCE_THRESHOLD` **or** high-stakes → route to review, regardless of self-reported confidence); `GET /api/forms/{id}/review`, `POST /api/forms/{id}/review` (approve/correct); `services/form_renderer.py` renders the approved output **and persists it to S3 via `services/storage.py`** (referenced from the `Form` record, so Phase 5's history can list/re-download it) + `GET /api/forms/{id}/download` — **blocked until all flagged fields resolved**; verify **never auto-submits**
- [ ] **Frontend:** Review page — confidence-coded fields (green verified / yellow low-conf / red missing or high-stakes), one-click approve/edit each, side-by-side with source doc; download button disabled until review complete
- [ ] **Done when:** a form with flagged fields cannot be downloaded until the user approves/corrects each; approved output downloads as a filled PDF, never submitted

---

## Phase 4 — Schema inference for unseen forms (UC3, FR4)
The hardest, most differentiating path — do it only after the known-template path is solid.

- [ ] **DB:** mark `Form.schema_source` (`template` vs `inferred`) for auditability/metrics
- [ ] **Backend:** `form_schema_tool` inference branch — extract required fields from the uploaded form itself when no template matches; inferred fields default to lower confidence (→ more land in review, as expected)
- [ ] **Frontend:** surface "inferred form" state so the user knows more fields need review
- [ ] **Done when:** upload a form with no template → system extracts its fields, fills what it can, routes the rest to review

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