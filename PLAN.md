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

## Phase 1 — Profile ingestion from ID documents (UC1, FR1/FR2/FR12)
Upload an ID doc → vision-LLM extracts it → encrypted profile you can see. First real feature.

- [ ] **DB:** `Document` model (metadata, `s3_key`, `ocr_status`); `Profile` + `ProfileField` (field-level **encrypted** values + `source_doc_id` provenance)
- [ ] **Backend:** `services/storage.py` (S3/MinIO put/get); `core/encryption.py` (field-level PII encrypt/decrypt); `POST /api/documents/upload` → enqueue Celery `ocr_extract_task`; `services/ocr/vision_llm.py` (primary) + `services/extraction.py` (strict-schema JSON); `GET /api/documents/{id}/status`; `GET /api/profile`
- [ ] **Frontend:** Upload page with drag-drop + camera capture, job-status polling, profile view of extracted fields
- [ ] **Done when:** upload an Aadhaar/PAN image → poll → see extracted, encrypted-at-rest profile fields with source-doc provenance

---

## Phase 2 — Known-template form fill (UC2, FR3/FR5/FR6)
Upload a form the system has a template for → agent maps profile data → draft with per-field
confidence. Auto-fill path only; review UI comes in Phase 3.

- [ ] **DB:** `Form` + `FormField` (**encrypted** value via `core/encryption.py` from Phase 1, `profile_field_id` FK + `source_doc_id` provenance, `confidence`, `needs_review`, `reviewed`); seed `templates/income_certificate.json` (+1 more)
- [ ] **Backend:** LangGraph `agent/graph.py` wiring `form_schema_tool` (known-template branch) → `profile_lookup_tool` (phrasing/format variance) → `confidence_scorer_tool`; confidence here is a **provisional** score derived from the profile field's own extraction-time confidence/provenance (set in Phase 1) — there is no fresh cross-check of the *mapped/formatted* value yet, that's Phase 3; `POST /api/forms/upload` → enqueue `fill_form_task`; `GET /api/forms/{id}`
- [ ] **Frontend:** form upload, draft view listing each field with its provisional confidence score
- [ ] **Done when:** upload a known form type → agent produces a draft where each field shows a provisional confidence score sourced from profile data

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