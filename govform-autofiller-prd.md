# PRD: Autonomous Government Form Auto-Filler Agent

**Project codename:** Project 7
**Document type:** Product Requirements Document
**Status:** Draft for build planning

---

## 1. Executive Summary

An AI agent that ingests a citizen's identity documents once (Aadhaar, PAN, marksheets, address proof), builds a verified personal data store, and then auto-fills any new Indian government form — passport renewal, income/caste/domicile certificates, scholarship applications — by semantically mapping form fields to known data, scoring its own confidence per field, and routing anything ambiguous or high-stakes to mandatory human review. It never auto-submits to a government portal; the output is a correctly filled, human-approved document ready for the citizen to submit themselves.

The core bet: existing autofill (Chrome, LinkedIn "Apply with Resume") solves *string matching on semantic HTML*. This project solves *semantic field matching on unstructured scanned bureaucratic paperwork with no API layer and no fixed schema* — a materially harder, currently-unsolved problem.

---

## 2. Problem Statement

Indian citizens re-enter the same core facts (name, DOB, address, parents' names, ID numbers) across dozens of disconnected bureaucratic forms, in inconsistent formats and phrasing. This causes:

- Transcription errors that trigger rejections and re-submission cycles
- Disproportionate burden on elderly and low digital-literacy users
- Repeated friction for students who file scholarship/certificate paperwork every academic year

**Why this is unsolved today:**

| Existing solution | What it actually does | Why it fails here |
|---|---|---|
| Chrome/Google autofill | Matches `input type`/`autocomplete` attributes to fixed categories | Govt forms are scanned PDFs/images or non-semantic HTML — nothing to pattern-match |
| LinkedIn "Apply with Resume" | Parses resume into a fixed schema, maps to forms LinkedIn/ATS partners control | Only works inside a closed, API-partnered ecosystem; govt portals have no such API |
| Manual re-entry (status quo) | Human reads ID, retypes into form | Error-prone, slow, hardest on the least digitally literate |

This project's differentiator is the combination of **vision-based extraction from physical ID documents**, **semantic (not string) field matching across non-standardized forms**, and a **verification/trust layer** that checks filled values against source documents — none of which existing autofill tools attempt.

---

## 3. Target Users & Use Cases

### Personas

1. **Student (primary)** — files scholarship applications, caste/income/domicile certificates repeatedly across academic years; low tolerance for rejection-and-resubmit delays.
2. **Working professional** — renews passport, PAN-Aadhaar linkage, address-change documents; time-constrained, wants speed over hand-holding.
3. **Elderly citizen** — struggles with digital forms generally; needs a high-trust, low-friction review UI more than raw speed.

### Use Cases

| # | Use case | Trigger | Flow | Outcome |
|---|---|---|---|---|
| UC1 | Onboard identity profile | User uploads Aadhaar, PAN, marksheet, address proof | OCR/vision extraction → structured JSON → encrypted profile store | Reusable verified data store, one-time setup |
| UC2 | Fill a known form type (e.g. income certificate) | User uploads a form PDF/image | Schema tool recognizes template → field mapping → confidence scoring → auto-fill high-confidence fields | Draft filled form with confidence highlighting |
| UC3 | Fill an unseen form type | User uploads a form the system has no template for | Schema-inference step extracts required fields from the form itself → maps to profile data with lower default confidence | Draft filled form, more fields flagged for review (expected) |
| UC4 | Human review of flagged fields | Any field <90% confidence, or involving money/legal declarations, or a non-exact date/ID match | Review UI shows only flagged fields, side-by-side with source document | User approves or corrects each field |
| UC5 | Re-use profile across multiple forms in a session | Student files 3 scholarship forms in one sitting | Profile fetched once, reused across all 3 fill operations | Cumulative time savings scales with number of forms |
| UC6 | Data deletion / profile purge | User requests account/data deletion | Cascade delete of profile + documents + form history | Compliance with data-minimization commitment |
| UC7 | Cross-check before finalizing | Any form nearing submission | `document_verification_tool` re-validates every field against source doc | Prevents silent drift between profile data and finalized form |

### Explicit Non-Use-Cases (Scope Boundaries)

- Does **not** auto-submit to any government portal (legal/ToS risk; most portals have no public API anyway)
- Does **not** retain data beyond operational need; deletion is a first-class feature, not an afterthought
- Is **not** a bulk/scraping-at-scale tool — explicitly single-user, personal-use scoped

---

## 4. Impact Scoring

Scored 1–5 per dimension (5 = highest), for portfolio/resume and real-world value assessment.

| Dimension | Score | Rationale |
|---|---|---|
| **Real-world problem severity** | 5 | Affects hundreds of millions of Indian citizens; rejection/resubmission cycles are a well-documented pain point |
| **Technical differentiation** | 5 | Semantic field-mapping over unstructured scans + a verification/trust layer is genuinely unsolved territory, not a wrapper over existing autofill |
| **Engineering depth (resume-worthiness)** | 4 | Touches OCR/vision, structured extraction, tool-calling agent orchestration, async job queues, PII security, HITL UX — broad and demonstrable |
| **Build feasibility (solo/small team, few weeks)** | 3 | Schema inference for unknown forms and OCR robustness on low-quality scans are genuinely hard; MVP with known-template forms is feasible faster |
| **Safety/trust design maturity** | 5 | No-auto-submit + confidence thresholds + mandatory HITL for high-stakes fields is a defensible, honest safety posture |
| **Market/monetization potential** | 2 | Legal/liability grey zone around govt paperwork limits commercial scaling without partnerships; strongest as a portfolio/social-impact project rather than a startup, at least initially |
| **Demo-ability** | 4 | Visually compelling (confidence-highlighted form fill, before/after) and easy to narrate in an interview or pitch |

**Composite impact score: 4.0 / 5** — high technical and narrative value, primarily as a demonstrable engineering project; monetization would need a partnerships-first strategy (e.g., NGOs, CSCs, university financial-aid offices) rather than direct-to-government integration.

---

## 5. Tech Stack: Options & Tradeoffs

### 5.1 OCR / Vision Extraction

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Tesseract (open source) | Free, offline, no API cost | Weak on handwriting, skewed scans, varied Indian ID layouts | Use only as a fallback / cost-saver for clean typed forms |
| Vision-LLM (Claude / GPT-4V) | Robust to handwriting, rotated/skewed scans, mixed-language text (Hindi/English), layout variance | Per-call cost, latency, needs prompt/schema engineering | **Chosen as primary** — reliability on messy real-world ID scans outweighs cost at this scale |

**Decision:** Vision-LLM primary, Tesseract as a cheap first-pass fallback for clearly clean, typed documents to save cost on the obvious cases.

### 5.2 Structured Field Extraction

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Regex / rule-based parsing | Fast, free | Brittle across format variance ("Father's Name" vs "Name of Father") | Rejected — defeats the project's core value proposition |
| LLM-based JSON-schema extraction | Handles semantic equivalence, phrasing variance, derived formats (date formats etc.) | Needs strict schema constraints to avoid hallucination | **Chosen** — this *is* the differentiator; must be paired with confidence scoring and verification, not trusted blindly |

### 5.3 Agent Orchestration

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Custom tool-calling loop (hand-rolled) | Full control, no framework overhead, easier to reason about for a well-defined fixed pipeline | More boilerplate, less resume "framework name recognition" | Good if optimizing for reliability and debuggability |
| LangGraph | Explicit state graph, good for the branching HITL logic (auto-fill vs. flag-for-review path), resume-recognizable | Added abstraction/learning overhead | **Chosen** — the HITL branching (auto-fill / flag / re-verify) maps naturally to a state graph, and framework recognizability matters for the stated placement goal |

### 5.4 Backend Framework

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| FastAPI (Python) | Async-native, pairs naturally with Python ML/LLM tooling, auto OpenAPI docs | Python concurrency ceiling vs. Node/Go for pure I/O at massive scale | **Chosen** — scale requirements here (single-user, personal use) don't need Go/Node-level throughput; Python keeps the ML and API layers in one language |
| Node/Express | Great async I/O | Splits stack language from the Python-based AI/ML layer, adds cross-language friction | Rejected for this project's scope |

### 5.5 Async Job Processing

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Celery + Redis | Mature, well-documented, natural fit with FastAPI, good retry semantics | Operational overhead (broker + workers to run) | **Chosen** — OCR/LLM calls are exactly the kind of slow, retry-able, queueable work Celery is built for |
| In-process async tasks only | Simpler ops, no extra infra | Blocks scaling beyond a few concurrent uploads, no durable retry | Rejected beyond a throwaway prototype |

### 5.6 Database

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| PostgreSQL | Strong support for field-level encryption, JSONB for flexible per-form-type schemas, mature ecosystem | None significant for this scale | **Chosen** |
| MongoDB | Schema flexibility for varied form types | Weaker native support for encrypted-field access control patterns needed for PII | Rejected — PII handling requirements favor Postgres's maturity here |

### 5.7 Object Storage

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| MinIO (local/dev) | Free, S3-compatible, easy local dev parity | Not production-grade at scale | Use for dev/demo |
| AWS S3 (prod) | Durable, standard, same API shape as MinIO | Cost at scale | **Chosen for production**, MinIO for local — S3-compatible API means zero code change between environments |

### 5.8 Deployment

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Kubernetes | Scales well, industry standard | Heavy operational overhead for a solo/small-team personal-use-scoped project | Overkill for MVP |
| Docker Compose → Fly.io / ECS | Simple to run, cheap, fast to iterate | Less "enterprise scale" story | **Chosen for MVP** — right-sized for actual expected load; Kubernetes is a "later" migration, not a v1 decision |

### 5.9 Auth

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| JWT + bcrypt | Simple, stateless, well-understood | Manual token refresh/revocation handling | **Chosen** — sufficient for this project's scope; OAuth/SSO is unnecessary complexity for a personal-data tool with no third-party login requirement |

---

## 6. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND (React + TS)                    │
│  ┌────────────┐  ┌───────────────────┐  ┌──────────────────┐    │
│  │ Doc Upload │  │ Form Preview /     │  │ History Dashboard│    │
│  │ (drag-drop,│  │ Confidence-Coded   │  │ (past forms,     │    │
│  │  camera)   │  │ Review/Edit Screen │  │  profile data)   │    │
│  └────────────┘  └───────────────────┘  └──────────────────┘    │
└───────────────────────────────┬───────────────────────────────────┘
                                 │ REST/JSON (JWT auth)
┌───────────────────────────────▼───────────────────────────────────┐
│                        BACKEND (FastAPI)                         │
│  Auth (JWT+bcrypt) │ Session mgmt │ API routes                   │
│                                 │                                 │
│           ┌─────────────────────▼─────────────────────┐           │
│           │     Async Job Queue (Celery + Redis)       │           │
│           │  - OCR/vision extraction jobs               │           │
│           │  - Field extraction jobs                    │           │
│           │  - Verification jobs                        │           │
│           │  (retry logic on failure)                   │           │
│           └─────────────────────┬─────────────────────┘           │
│                                 │                                 │
│           ┌─────────────────────▼─────────────────────┐           │
│           │       AGENT ORCHESTRATION (LangGraph)      │           │
│           │                                             │           │
│           │  ┌──────────────┐  ┌───────────────────┐   │           │
│           │  │form_schema_  │  │profile_lookup_tool │   │           │
│           │  │tool          │  │                    │   │           │
│           │  └──────┬───────┘  └─────────┬──────────┘   │           │
│           │         │                    │              │           │
│           │  ┌──────▼────────────────────▼──────────┐   │           │
│           │  │   document_verification_tool          │   │           │
│           │  └──────────────────┬────────────────────┘   │           │
│           │                     │                        │           │
│           │  ┌──────────────────▼────────────────────┐   │           │
│           │  │   confidence_scorer_tool               │   │           │
│           │  └──────┬───────────────────┬────────────┘   │           │
│           │         │ high conf.        │ low conf./     │           │
│           │         ▼                   ▼ high-stakes    │           │
│           │  ┌─────────────┐    ┌─────────────────────┐ │           │
│           │  │ Auto-fill   │    │ Route to HITL review │ │           │
│           │  └─────────────┘    └─────────────────────┘ │           │
│           └─────────────────────────────────────────────┘           │
└───────┬─────────────────────────────────────────────┬──────────────┘
        │                                              │
┌───────▼──────────┐                        ┌──────────▼───────────┐
│   PostgreSQL       │                        │  S3-compatible store │
│ (encrypted PII,     │                        │  (MinIO dev / S3 prod)│
│  profile + form     │                        │  raw document uploads │
│  history)           │                        │                       │
└─────────────────────┘                        └───────────────────────┘

External: Vision-LLM API (Claude/GPT-4V) for OCR + extraction; Tesseract fallback for clean typed docs.
```

### Data Flow (sequence)

1. User uploads ID documents (once) → OCR/vision extraction → structured JSON → encrypted profile store (UC1)
2. User uploads a new form → `form_schema_tool` identifies type + required fields (known template or inferred)
3. `profile_lookup_tool` fetches candidate values per field from profile store
4. `document_verification_tool` cross-checks each candidate value against the original source document
5. `confidence_scorer_tool` assigns a per-field score (exact source match = high; inferred/derived = lower; missing = flagged)
6. High-confidence fields auto-fill; low-confidence or high-stakes (money, legal declarations, non-exact dates/IDs) route to review UI
7. User approves/corrects flagged fields in review screen
8. Final filled form (PDF or structured output) generated for user download — **never auto-submitted**

---

## 7. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| FR1 | System shall extract structured data from Aadhaar, PAN, marksheets, address proof via vision-LLM OCR | Must |
| FR2 | System shall store extracted profile data encrypted at rest, field-level | Must |
| FR3 | System shall identify form type from an uploaded PDF/image, using known templates where available | Must |
| FR4 | System shall infer a field schema for forms not in the known-template library | Should |
| FR5 | System shall map form fields to profile data accounting for phrasing/format variance | Must |
| FR6 | System shall assign a confidence score to every filled field | Must |
| FR7 | System shall never auto-submit a form to any external portal | Must |
| FR8 | System shall route any field below the confidence threshold, or involving money/legal declarations/date-ID mismatches, to mandatory human review | Must |
| FR9 | System shall present a review UI with field-level highlighting (green/yellow/red) | Must |
| FR10 | System shall allow the user to delete their profile and all associated documents/history | Must |
| FR11 | System shall maintain a history of past filled forms for reuse reference | Should |
| FR12 | System shall support camera capture upload for mobile users | Should |

---

## 8. Non-Functional Requirements

| Category | Requirement |
|---|---|
| **Security** | PII encrypted at rest and in transit; field-level access control; JWT-based auth with bcrypt password hashing |
| **Privacy** | Explicit, easy-to-find data deletion; no data retention beyond operational need; no third-party data sharing |
| **Reliability** | Async job retry logic for failed OCR/LLM calls; jobs must not silently drop |
| **Performance** | Full pipeline (upload → filled form ready) latency should be reported and tracked as a core metric, not assumed |
| **Usability** | Review screen must be fast to use — one-click approve/edit per field, not a wall of raw text |
| **Auditability** | Every auto-filled field should be traceable to the source document and the confidence score that justified auto-fill |

---

## 9. Success Metrics

| Metric | Definition | Why it matters |
|---|---|---|
| % fields auto-filled at high confidence | (auto-filled fields) / (total fields) per form | Core measure of how much manual work is actually eliminated |
| Accuracy of auto-filled fields | Auto-filled value vs. ground truth, on a test set of real/dummy forms | Confidence scoring is worthless if it doesn't correlate with correctness |
| Time saved per form | Manual fill time vs. review-and-approve time | Direct user-facing value proposition |
| End-to-end pipeline latency | Upload → filled form ready | Determines whether the tool feels responsive or is abandoned mid-flow |
| Schema-inference success rate | % of unseen form types correctly parsed without a pre-built template | Signals how far the "hardest part" (generalization) actually generalizes |

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LLM hallucinates a field value with false confidence | Confidence scorer must weight "exact match to source doc" far above "inferred/derived"; never treat LLM self-reported confidence as ground truth alone |
| Sensitive PII breach | Field-level encryption, minimal retention, explicit deletion flow, no unnecessary logging of raw PII |
| Form schema drift (govt changes a form layout) | Schema-inference fallback path ensures known-template failures degrade gracefully rather than breaking entirely |
| Users treating auto-fill as "final" and skipping review | UI must make review a required step for flagged fields before download is enabled, not an optional afterthought |
| Legal/liability exposure from being seen as a "government form submission" tool | Explicit, visible scope statement: output is for the user's own review and manual submission only; no auto-submission feature ever built |

---

## 11. Suggested Build Order (high level)

1. Profile ingestion pipeline (OCR/vision → structured JSON → encrypted store) — UC1
2. Known-template form fill for 2–3 common forms (income certificate, scholarship form) — UC2
3. Confidence scoring + HITL review UI — UC4
4. Schema-inference path for unseen forms — UC3 (hardest, do after the known-path is solid)
5. History dashboard + data deletion flow — UC5, UC6
6. Metrics instrumentation (latency, accuracy, time-saved tracking) across the pipeline

---

## 12. Resume-Ready Summary

> Built an end-to-end AI agent system that auto-fills bureaucratic forms by extracting data from ID documents (OCR + vision-LLM), using a LangGraph-based tool-calling architecture with confidence-scored field verification and mandatory human-in-the-loop review; deployed with async job processing (Celery/Redis) handling concurrent document uploads, with field-level PII encryption throughout.
