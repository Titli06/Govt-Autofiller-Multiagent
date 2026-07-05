# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

This repo is a **scaffold**: the directory structure, config, and stub modules exist, but the implementation is almost entirely `# TODO`. The authoritative spec is [govform-autofiller-prd.md](govform-autofiller-prd.md) ("Project 7"). Read it before writing code — the sections below distill the decisions that constrain implementation. There is no git history yet (`git init` when ready). Stub files carry a module docstring stating each file's responsibility; fill them in following the build order below.

## Repo layout

```
backend/              FastAPI + Celery + LangGraph service (Python 3.11)
  app/
    main.py           FastAPI entrypoint; mounts /api/* routers
    config.py         pydantic-settings, reads .env (incl. CONFIDENCE_THRESHOLD)
    core/             security.py (JWT+bcrypt), encryption.py (field-level PII), logging.py (PII-safe)
    api/routes/       auth, documents, profile, forms, history
    agent/            LangGraph orchestration
      graph.py        the state graph (node order + HITL branch)
      state.py        AgentState / FieldResult TypedDicts
      tools/          form_schema, profile_lookup, document_verification, confidence_scorer
    services/         ocr/ (vision_llm primary, tesseract fallback), extraction, storage (S3), form_renderer
    models/           SQLAlchemy: user, profile, document, form
    schemas/          Pydantic request/response contracts
    templates/        known-form JSON templates (e.g. income_certificate.json)
    workers/          celery_app.py + tasks.py (async OCR/fill/verify jobs)
    metrics/          pipeline instrumentation (latency, auto-fill %, accuracy, ...)
    db/               session, declarative base, migrations/ (Alembic)
  tests/
frontend/             React + TypeScript (Vite)
  src/                pages/ (Upload, Review, History), components/ (ConfidenceField), api/client.ts, types/
docker-compose.yml    local stack: postgres, redis, minio, api, worker, frontend
.env.example          copy to .env; holds secrets, DB/Redis/S3 URLs, GEMINI_API_KEY
```

## Commands

The whole stack runs via Docker Compose; the backend and frontend can also run standalone.

```bash
cp .env.example .env          # then fill in GEMINI_API_KEY and the secrets
docker compose up             # postgres, redis, minio, api (:8000), worker, frontend (:5173)
```

Backend (from `backend/`, Python 3.11):
```bash
pip install -e ".[dev]"                              # install app + dev deps
uvicorn app.main:app --reload                        # API at http://localhost:8000 (docs at /docs)
celery -A app.workers.celery_app worker --loglevel=info   # async job worker (needs Redis)
pytest                                               # run tests
pytest tests/test_confidence_scorer.py -k high_stakes     # single test / filter
ruff check . && mypy app                             # lint + typecheck
alembic revision --autogenerate -m "msg" && alembic upgrade head   # DB migrations
```

Frontend (from `frontend/`):
```bash
npm install
npm run dev        # Vite dev server at http://localhost:5173
npm run build      # tsc -b + vite build
npm run lint
npm test           # vitest
```

## What this system does (one paragraph)

A citizen uploads identity documents once (Aadhaar, PAN, marksheets, address proof). A vision-LLM extracts them into a verified, encrypted profile store. When the user later uploads a blank government form, an agent identifies the form's fields, maps them semantically to the profile data, scores its confidence per field, auto-fills high-confidence fields, and routes everything else to mandatory human review. The output is a filled, human-approved document the user downloads and submits themselves. The hard problem being solved is *semantic field matching over unstructured scanned bureaucratic paperwork with no API and no fixed schema* — not string/attribute matching like browser autofill.

## Non-negotiable constraints (these are product requirements, not preferences)

- **Never auto-submit to any government portal.** No auto-submission feature is ever to be built (FR7). Output is always a downloadable draft for the user to submit manually. This is a legal/liability boundary, not an optimization.
- **Mandatory human-in-the-loop review** for any field that is below the confidence threshold (~90%), involves money or legal declarations, or is a non-exact date/ID match (FR8). Review must be a *required, download-blocking* step for flagged fields — not optional.
- **Confidence must be grounded in source-document match, not LLM self-report.** An exact match to the original document scores high; inferred/derived values score lower; missing data is flagged. Never trust the model's self-reported confidence alone — this is the primary hallucination mitigation.
- **PII is encrypted at rest at the field level** (FR2) and in transit. Data deletion (profile + documents + history cascade) is a first-class feature (FR10), not an afterthought. Avoid logging raw PII.
- **Every auto-filled field must be auditable** back to the source document and the confidence score that justified auto-filling it.

## Chosen tech stack (decided in PRD §5 — don't re-litigate without reason)

- **Backend:** FastAPI (Python) — keeps ML/LLM and API layers in one language.
- **Agent orchestration:** LangGraph — the auto-fill / flag-for-review / re-verify branching maps to a state graph.
- **OCR/vision:** Vision-LLM as primary; Tesseract only as a cheap first-pass fallback for clearly clean, typed documents. PRD §5.1 named Claude/GPT-4V; **swapped to Google Gemini** during Phase 1 build (explicit later decision — see `services/ocr/vision_llm.py` docstring and memory/phase1-decisions.md). The module's interface (`extract()`, `RawExtraction`, `VisionExtractionError`) is provider-agnostic, so this can swap again without touching `extraction.py` or `workers/tasks.py`.
- **Structured extraction:** LLM-based JSON-schema extraction with strict schema constraints (regex/rule-based parsing was explicitly rejected — it defeats the core value prop). Must always be paired with confidence scoring + verification.
- **Async jobs:** Celery + Redis — OCR/LLM calls are slow, retryable, queueable work; jobs must not silently drop.
- **Database:** PostgreSQL (field-level encryption + JSONB for per-form-type schemas).
- **Object storage:** S3-compatible — MinIO for local/dev, AWS S3 for prod (same API, zero code change between envs).
- **Auth:** JWT + bcrypt.
- **Frontend:** React + TypeScript.
- **Deploy:** Docker Compose → Fly.io / ECS for MVP. Kubernetes is explicitly out of scope for v1.

## Agent architecture (the core pipeline)

The LangGraph agent orchestrates four tools in sequence, then branches on confidence:

1. `form_schema_tool` — identifies the form type and its required fields. Uses a known template when one exists; **infers the schema from the form itself** when it doesn't (UC3/FR4 — the hardest, most differentiating path; build it *after* the known-template path is solid).
2. `profile_lookup_tool` — fetches candidate values per field from the encrypted profile store, accounting for phrasing/format variance ("Father's Name" vs "Name of Father").
3. `document_verification_tool` — cross-checks each candidate value against the original source document (UC7). This is what prevents silent drift between profile data and the finalized form.
4. `confidence_scorer_tool` — assigns a per-field score. High → auto-fill. Low or high-stakes → route to HITL review UI.

Data flow end-to-end: upload → OCR/vision extraction → structured JSON → encrypted profile (once); then per form: schema ID → profile lookup → verification → confidence scoring → auto-fill vs. review branch → user approval → downloadable filled form.

## Suggested build order (PRD §11)

1. Profile ingestion pipeline (OCR/vision → structured JSON → encrypted store) — UC1.
2. Known-template form fill for 2–3 common forms (income certificate, scholarship form) — UC2.
3. Confidence scoring + HITL review UI (field-level green/yellow/red highlighting) — UC4.
4. Schema-inference path for unseen forms — UC3 (do this after the known path works).
5. History dashboard + data-deletion cascade — UC5, UC6.
6. Metrics instrumentation across the pipeline — see below.

## Metrics to instrument (they are part of the deliverable, PRD §9)

Pipeline latency (upload → filled form ready) must be *measured and reported*, not assumed. Also track: % of fields auto-filled at high confidence, accuracy of auto-filled fields vs. ground truth (confidence scoring is worthless if it doesn't correlate with correctness), time saved per form, and schema-inference success rate on unseen forms.
