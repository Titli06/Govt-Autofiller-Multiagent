# SPEC — Phase 2: Known-Template Form Fill

Scope-locked spec for **Phase 2 only** of [PLAN.md](PLAN.md). Ships one end-to-end vertical
slice — DB → backend → LangGraph agent → async worker → frontend — delivering the second real
product feature: a user uploads a **blank government form the system has a template for**, an
agent identifies the form, maps each required field to the user's encrypted profile data, and
produces a **draft** where every field carries a **provisional** confidence score, its source
provenance, and a computed (not-yet-enforced) review flag.

> **Authority:** [PLAN.md](PLAN.md) Phase 2 + [govform-autofiller-prd.md](govform-autofiller-prd.md)
> UC2, FR3/FR5/FR6 (and the *computation*, not enforcement, of FR8's review flags), §5.3
> (agent orchestration via LangGraph), §6 (data flow steps 2–3, 5), §8 (security/auditability
> NFRs), §10 (hallucination + PII risks), and [CLAUDE.md](CLAUDE.md) (confidence grounded in
> source data; PII encrypted field-level; no raw-PII logging; every filled value auditable to a
> source document + the score that justified it; **never** auto-submit).
>
> Builds on Phase 0 ([SPEC.md](SPEC.md)) — auth, `get_current_user`, sync SQLAlchemy 2.0,
> Celery — and Phase 1 ([SPEC-PHASE1.md](SPEC-PHASE1.md)) — the encrypted multi-candidate
> `ProfileField` store, `core/encryption.py` (AES-256-GCM + masking), `services/storage.py`
> (S3/MinIO), `services/preprocessing.py`, and the `ocr_status`/async-job pattern this phase
> mirrors. **Reuses the Google Gemini vision provider** established in Phase 1
> (`services/ocr/vision_llm.py`) for form classification.
>
> Where PLAN and PRD are silent, the decisions below were made in the Phase 2 build interview
> and are **binding for this phase**.

---

## 1. Objectives & Done-When

**Done when:** an authenticated user selects a known form type (income certificate or
scholarship application) and uploads the blank form (drag-drop or mobile camera) → the form is
stored in S3/MinIO and an async fill job runs the LangGraph pipeline → the user polls status →
on success they see a **draft** listing every required field with: its filled value (masked for
Aadhaar/PAN), a **provisional confidence** score + green/yellow/red band, whether it is flagged
for review and why, and its source provenance (which profile candidate + source ID document).
No form is ever submitted anywhere; the draft is read-only in Phase 2 (approve/correct/download
is Phase 3).

Acceptance is enumerated in §13.

### In scope
- **DB:** `Form` (metadata + `s3_key` + fill `status`), `FormField` (**field-level-encrypted**
  filled value, `profile_field_id` + `source_doc_id` provenance, provisional `confidence`,
  `needs_review` + `review_reason`, `reviewed` placeholder). One Phase-2 Alembic migration
  (`0003_forms`).
- **Templates:** keep `templates/income_certificate.json` (extend with a per-field `format`);
  add **`templates/scholarship_application.json`**. A small template registry loads them.
- **Agent:** implement `agent/graph.py` (`build_graph()`) wiring three tools in order —
  `form_schema_tool` (known-template branch + vision confirm) → `profile_lookup_tool`
  (deterministic `profile_key` mapping + candidate selection + format transform) →
  `confidence_scorer_tool` (provisional score + review-flag computation). Extend
  `agent/state.py` (`AgentState`/`FieldResult`).
- **Vision:** add `classify_form(images, known_types)` to the Gemini provider
  (`services/ocr/vision_llm.py`) — classify a blank form into one of the known template types,
  or `unknown`.
- **Worker:** implement `fill_form_task` (mirrors `ocr_extract_task`: bounded retry, idempotent
  delete-then-insert, always a terminal status, no raw PII in logs).
- **API:** `POST /api/forms/upload`, `GET /api/forms/{id}`.
- **Frontend:** a form-fill page (form-type select + drag-drop/camera upload + status polling)
  and a **read-only draft view** listing each field with its confidence band, review badge, and
  source. Reuses Phase 1's `ConfidenceField`.
- **Form types this phase:** **income certificate + scholarship application** only.

### Out of scope (defer to later phases)
- **`document_verification_tool`** — the fresh cross-check of the *mapped/formatted* form value
  against the source document. **Phase 3.** In Phase 2 the graph node order leaves the exact
  seam for it (**between** `profile_lookup` and `confidence_scorer`); confidence here is
  explicitly **provisional**, inherited from the profile candidate's own extraction-time score,
  never a fresh verification. `FieldResult.verified` stays `False`.
- **HITL enforcement:** the review UI, per-field approve/correct, the `draft → in_review →
  approved` lifecycle, and **download gating**. **Phase 3.** Phase 2 *computes and stores*
  `needs_review`/`review_reason` but blocks nothing and renders the draft read-only. `GET
  /api/forms/{id}/review`, `POST .../review`, `GET .../download` stay stubs.
- **Form rendering / PDF output** (`services/form_renderer.py`) — **Phase 3**.
- **Schema inference** for forms with no template (`form_schema_tool` inference branch) —
  **Phase 4**. A form the LLM confidently recognizes as a *different known type* than the user
  declared is rejected as `type_mismatch`; a genuinely unrecognized form is not handled here.
- **LLM semantic field matching** — Phase 2 maps via the template's baked-in `profile_key`
  (deterministic). The semantic-matching path is exercised in **Phase 4** where labels are
  unknown.
- **History / reuse dashboard** (`GET /api/history`) and **deletion cascade** — **Phase 5**.
  `Form`/`FormField` FKs are declared so the Phase 5 purge is a one-liner; no endpoint now.
- **Metrics dashboards** — **Phase 6**. Phase 2 only records `created_at → filled_at` so
  fill latency and the auto-fill/flag split are *recoverable*, not dashboarded.

---

## 2. Decisions carried from the interview (binding for Phase 2)

| # | Area | Decision |
|---|---|---|
| 1 | **Form identification** | **User declares, LLM confirms.** User picks the form type from the known-template list at upload. The vision-LLM classifies the uploaded blank form; on **positive disagreement** (it confidently recognizes a *different known* type) the form is flagged `type_mismatch` and **nothing is filled** — mirrors Phase 1's doc-type safety. An `unknown`/uncertain classification does **not** block (trust the user's declared type). |
| 2 | **Field mapping** | **Deterministic via the template's `profile_key`.** Known templates already map each form field to a canonical profile key; Phase 2 fetches the best profile candidate for that key. No LLM semantic matching this phase (that's Phase 4, for unlabeled/inferred forms). Fast, cheap, fully unit-testable. |
| 3 | **Candidate selection** | **Confidence-first, user-acted wins.** When a `profile_key` has multiple candidates (e.g. `full_name` from both Aadhaar and PAN), pick by: (a) `user_corrected`/`user_confirmed` first, then (b) highest grounded `confidence`, then (c) most recent (`created_at`). |
| 4 | **Formatting** | **Reformat, record, keep score.** Apply the template's per-field `format` (e.g. DOB `YYYY-MM-DD → DD/MM/YYYY`, name uppercase, address single-line). Record that a transform happened (`transformed=true`) so Phase 3's verification re-checks it; the **provisional confidence carries over unchanged** from the profile candidate. |
| 5 | **Review flags** | **Compute now, enforce later.** `confidence_scorer_tool` computes `needs_review` + `review_reason` in the draft (low confidence, high-stakes, missing, unverified source). Phase 3 adds the blocking review UI + document verification. The columns already exist on `FormField`. |
| 6 | **Unfillable fields** | **Blank, flagged, reason-typed.** A required field with no value appears in the draft empty (`value=null`, `confidence=0`, `needs_review=true`), with the reason distinguishing **`no_mapping`** (`profile_key` is null in the template) from **`no_candidate`** (mapped, but the profile has no such value). |
| 7 | **Second template** | **Scholarship application.** PRD's primary persona (student); overlaps income-cert fields and adds student-specific unmapped fields (`institution_name`, `course_name`) that exercise the `no_mapping` path. |
| 8 | **Form storage & status** | **Own `Form.s3_key` + fill status.** Store the blank form in the private S3 bucket on its own `Form` record (needed for Phase 3 rendering + Phase 4 inference). Status `pending → processing → (filled \| failed \| type_mismatch)`, mirroring `ocr_status`. Phase 3 adds `in_review`/`approved`. |
| 9 | **Provisional score** | **Inherit; user-acted → 1.0.** A form field's provisional confidence = the chosen candidate's grounded confidence; if that candidate was `user_confirmed`/`user_corrected`, treat it as **1.0**. Missing → **0**. A format transform does not change it (Decision 4). |
| 10 | **Upstream trust propagation** | **Propagate.** If the chosen source candidate is itself unresolved (`needs_confirmation` or `failed_validation`), the form field is flagged `needs_review` with reason `unverified_source`, regardless of its numeric score. Trust does not launder through the fill step. |
| 11 | **Transform ≠ auto-flag** | **Record only.** A format transform is recorded (`transformed=true`) for Phase 3 to re-verify, but does **not** by itself set `needs_review` in Phase 2. |
| 12 | **Spec location** | This file — `SPEC-PHASE2.md`. `SPEC.md` (Phase 0) / `SPEC-PHASE1.md` unchanged. |

### Default implementation choices (not interviewed; set here)
- **Known form types** are exactly the template files present in `app/templates/`:
  `income_certificate`, `scholarship_application`. The upload `form_type` enum is derived from
  the registry, not hard-coded twice.
- **No profile yet:** a user who uploads a form before ingesting any ID document still gets a
  valid draft — every field lands `no_candidate`/`no_mapping` and flagged. The form is still
  `filled` (a draft can be all-flagged); this is informative, not an error.
- **Mask by canonical key:** `value_masked`/display masking keys off the mapped **`profile_key`**
  (Aadhaar/PAN → masked), not the form's field name.
- **FormField snapshots the value:** the (possibly reformatted) filled value is encrypted and
  stored on `FormField` (with a `profile_field_id` pointer back to the candidate). The form value
  can legitimately differ from the profile value (reformatting), so it is snapshotted, not
  re-derived on read.
- **Draft success is `filled`, not `partial`:** a draft always contains *all* template fields
  (filled or flagged). Missing fields are flagged *within* a `filled` draft — there is no
  `partial` form status (unlike Phase 1 documents). `failed` is reserved for pipeline errors.
- **Pure graph nodes, DB/crypto in the task:** `fill_form_task` does all DB + decrypt/encrypt;
  it hands the graph an in-memory **decrypted profile snapshot** and the form images via the
  invocation config, so the LangGraph nodes/tools are pure and testable with fakes (no DB, no
  real API call).
- **DB access / IDs / timestamps:** same as Phases 0–1 — sync SQLAlchemy 2.0, `psycopg` v3,
  UUIDv4 PKs, `TIMESTAMP WITH TIME ZONE` UTC with `now()` server defaults.

---

## 3. Templates

Templates are the Phase-2 contract for *what a known form needs*. Loaded by a small registry
(`form_schema_tool`) that reads `app/templates/*.json` once and validates the shape at startup.

### 3.1 Template shape
```jsonc
{
  "form_type": "income_certificate",        // registry key == filename stem
  "display_name": "Income Certificate",
  "required_fields": [
    {
      "name": "date_of_birth",              // the form's field
      "profile_key": "dob",                 // canonical Phase-1 key, or null if unmapped
      "high_stakes": true,                  // FR8 category (money/legal/date/ID)
      "format": "date:%d/%m/%Y"             // Phase-2 addition; see §3.3 (default "as_is")
    }
  ]
}
```
`profile_key` MUST be one of the Phase-1 canonical vocabulary values (SPEC-PHASE1 §3.1:
`full_name`, `father_name`, `dob`, `gender`, `address`, `aadhaar_number`, `pan_number`) or
`null`. Registry validation rejects any other key so a template typo can't silently produce
`no_candidate` everywhere.

### 3.2 The two Phase-2 templates

**`income_certificate.json`** (existing — add `format`):

| `name` | `profile_key` | `high_stakes` | `format` |
|---|---|---|---|
| `applicant_name` | `full_name` | false | `as_is` |
| `father_name` | `father_name` | false | `as_is` |
| `date_of_birth` | `dob` | **true** | `date:%d/%m/%Y` |
| `address` | `address` | false | `single_line` |
| `annual_income` | `null` | **true** | `as_is` |
| `aadhaar_number` | `aadhaar_number` | **true** | `as_is` |

**`scholarship_application.json`** (new):

| `name` | `profile_key` | `high_stakes` | `format` |
|---|---|---|---|
| `applicant_name` | `full_name` | false | `as_is` |
| `father_name` | `father_name` | false | `as_is` |
| `date_of_birth` | `dob` | **true** | `date:%d/%m/%Y` |
| `gender` | `gender` | false | `as_is` |
| `address` | `address` | false | `single_line` |
| `institution_name` | `null` | false | `as_is` |
| `course_name` | `null` | false | `as_is` |
| `annual_income` | `null` | **true** | `as_is` |
| `aadhaar_number` | `aadhaar_number` | **true** | `as_is` |

`institution_name`/`course_name` (`profile_key: null`) deliberately exercise the `no_mapping`
draft path. `annual_income` exercises `no_candidate` (mapped conceptually to money, but the
profile has no such field → still null `profile_key`, reason `no_mapping`; a genuine
`no_candidate` occurs when e.g. `pan_number` is templated but the user never uploaded a PAN).

### 3.3 Supported `format` transforms (Phase 2)
Applied by `profile_lookup_tool` to the selected candidate's plaintext value:

| `format` | Behavior | Example |
|---|---|---|
| `as_is` (default) | verbatim | `RAVI KUMAR` → `RAVI KUMAR` |
| `date:<strftime>` | parse ISO `YYYY-MM-DD` → reformat; on parse failure, leave value + still mark `transformed` false and let scoring flag it | `1998-04-12` → `12/04/1998` |
| `upper` | uppercase | `Ravi Kumar` → `RAVI KUMAR` |
| `single_line` | collapse newlines/runs of whitespace to single spaces | multi-line address → one line |

A transform that actually changes the string sets `transformed=true`. `as_is`, or a transform
whose output equals its input, leaves `transformed=false`. Unknown/unsupported `format` strings
are rejected at registry-validation time (fail fast, not mid-fill).

---

## 4. Data Model

Two tables (+ FKs into Phase 0's `users`, Phase 1's `documents`/`profile_fields`). One Phase-2
Alembic migration (`0003_forms`); `db/base.py` must import the new models so autogenerate sees
them.

### 4.1 `forms` (`models/form.py`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → users.id | `ON DELETE CASCADE`, indexed |
| `declared_form_type` | text, not null | user's pick: `income_certificate` \| `scholarship_application` |
| `detected_form_type` | text, nullable | vision classification result (`…` \| `unknown`) |
| `s3_key` | text, not null | blank-form object key in the private bucket |
| `content_type` | text | original upload MIME (post-HEIC-convert value if changed) |
| `byte_size` | int | |
| `page_count` | int, nullable | for PDFs |
| `status` | text, not null | `pending` → `processing` → (`filled` \| `failed` \| `type_mismatch`) |
| `fill_error` | text, nullable | **safe, non-PII** reason on failure/mismatch |
| `filled_at` | timestamptz, nullable | set when the fill finishes (latency = `filled_at − created_at`) |
| `created_at` / `updated_at` | timestamptz | server default `now()` / on-update |

> Phase 3 extends `status` with `in_review`/`approved`; do not add them now.

### 4.2 `form_fields` (`models/form.py`)
One row per template `required_field`, always written (filled or flagged).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `form_id` | UUID FK → forms.id | `ON DELETE CASCADE`, indexed |
| `field_name` | text, not null | the **form's** field name (`applicant_name`, …) |
| `profile_key` | text, nullable | canonical key mapped (null ⇒ `no_mapping`) |
| `value_encrypted` | bytea, **nullable** | AES-256-GCM of the filled/reformatted value; **null when unfillable** |
| `value_masked` | text, nullable | display-safe masked form; required for high-sensitivity `profile_key` (Aadhaar/PAN), else null |
| `profile_field_id` | UUID FK → profile_fields.id | **nullable**, `ON DELETE SET NULL` — provenance: which candidate filled it |
| `source_doc_id` | UUID FK → documents.id | **nullable**, `ON DELETE SET NULL` — provenance: originating source document (copied from the candidate) |
| `confidence` | float, not null | provisional score `[0,1]` (§6.4); 0 when missing |
| `confidence_band` | text, not null | `high` \| `medium` \| `low` (derived; stored for query/metrics) |
| `high_stakes` | bool, not null | from the template field |
| `transformed` | bool, not null, default false | a format transform changed the value (§3.3) |
| `needs_review` | bool, not null | computed now, enforced Phase 3 |
| `review_reason` | text, nullable | single, precedence-ordered (§6.5); null when `needs_review` false |
| `reviewed` | bool, not null, default false | **Phase 3** sets this; always false in Phase 2 |
| `flags` | jsonb, nullable | full audit of *all* reasons: `{missing, high_stakes, unverified_source, low_confidence, transformed}` |
| `created_at` / `updated_at` | timestamptz | |

**Constraints & indexes:**
- `UNIQUE (form_id, field_name)` — one row per template field per form; a re-run replaces via
  delete-then-insert.
- index on `(form_id)` — draft rendering.

`ON DELETE SET NULL` on the provenance FKs (not CASCADE): a Phase-5 profile/document purge must
not delete already-generated form drafts — the encrypted snapshot value stays, only the pointer
is nulled. (The Phase-5 cascade deletes forms via the `user_id` path, separately.)

---

## 5. Config, deps & env additions

`config.py` — no new **required** settings. Reuses Phase-1 upload limits
(`max_upload_bytes`, `allowed_upload_content_types`, `max_upload_pages`), the confidence policy
(`confidence_threshold` = 0.90, `ocr_confidence_high`/`_medium` for banding), retry knobs
(`ocr_max_retries`, `ocr_retry_backoff_seconds` — reused by `fill_form_task`), the S3 settings,
and the Gemini settings (`gemini_api_key`, `vision_model`). Add if convenient:

```
# Form fill worker (Phase 2) — default to the OCR knobs unless overridden.
fill_max_retries: int = 3
fill_retry_backoff_seconds: int = 5
```

**Dependencies (`backend/pyproject.toml`):** add **`langgraph`** (+ its `langchain-core`
transitive). `google-genai`, `boto3`, `cryptography`, `pillow`/`pillow-heif`/`pypdf` already
present from Phase 1. No new dev deps beyond what's installed.

**`.env.example`:** no new required secrets (Gemini + PII key + S3 already documented in
Phase 1). Optionally note the two new `FILL_*` knobs.

---

## 6. Backend components

### 6.1 `agent/state.py` — extend the state contract
Extend the existing `TypedDict`s (keep Phase-1-compatible field names):

```python
class FieldResult(TypedDict):
    field_name: str
    profile_key: str | None
    value: str | None
    source_doc_id: str | None
    profile_field_id: str | None
    high_stakes: bool
    transformed: bool
    verified: bool              # ALWAYS False in Phase 2; Phase 3 verification sets it
    confidence: float
    confidence_band: str
    needs_review: bool
    review_reason: str | None   # single, precedence-ordered (§6.5)
    flags: dict                 # full audit of all triggers

class AgentState(TypedDict):
    user_id: str
    form_id: str
    declared_form_type: str
    detected_form_type: str | None
    type_mismatch: bool
    form_type: str | None       # resolved (== declared unless mismatch)
    field_specs: list[dict]     # template required_fields, loaded by form_schema node
    fields: list[FieldResult]
```

### 6.2 `agent/graph.py` — `build_graph()`
Compiles a `StateGraph[AgentState]` with three nodes, in order:

```
form_schema ──(type_mismatch?)──▶ END
     │ no
     ▼
profile_lookup ──▶ confidence_scorer ──▶ END
```

- Node functions are **pure over `(state, config)`**; all external inputs — the decrypted
  **profile snapshot**, the form **images**, and the **classifier callable** — are injected via
  the LangGraph invocation `config["configurable"]`. No node touches the DB or does crypto.
  This makes the graph testable by invoking it with a fake snapshot + a stub classifier.
- **`form_schema` node** → calls `form_schema_tool`:
  - `load_template(declared_form_type)` from the registry → `field_specs`, `form_type`.
  - `classifier(images, known_types)` → `detected_form_type`.
  - **Mismatch rule (Decision 1):** `type_mismatch = detected in known_types and detected !=
    declared`. `unknown`/uncertain ⇒ **not** a mismatch (proceed on the declared type).
  - Conditional edge: `type_mismatch` → `END`; else → `profile_lookup`.
- **`profile_lookup` node** → `profile_lookup_tool` (§6.3), pure over the injected snapshot.
- **`confidence_scorer` node** → `confidence_scorer_tool` (§6.4–6.5), pure.

> The Phase-3 `document_verification_tool` is inserted **between** `profile_lookup` and
> `confidence_scorer`. Leave that edge obvious; do not build the node now.

### 6.3 `agent/tools/form_schema_tool.py` & `profile_lookup_tool.py`

**`form_schema_tool`** — the template registry + known-template branch:
- `load_registry()` — read + validate every `app/templates/*.json` once (shape, `profile_key`
  vocabulary, `format` grammar). Cache. `known_types()` returns the registry keys.
- `load_template(form_type) -> Template` — the field specs for the fill.
- (The vision confirm is the injected `classifier`; `form_schema_tool` only owns the
  template side + the mismatch decision helper.)
- **Inference branch stays a stub** — Phase 4.

**`profile_lookup_tool`** — deterministic mapping over the decrypted snapshot:
- Input: `field_specs` + a `ProfileSnapshot` = `{profile_key: [CandidateView, …]}` where
  `CandidateView = {profile_field_id, source_doc_id, doc_type, value(plaintext), confidence,
  status, created_at}` (built by the task, §6.6).
- Per spec:
  - `profile_key is None` → `FieldResult(value=None, …)` tagged missing `no_mapping`.
  - no candidates for `profile_key` → `value=None`, missing `no_candidate`.
  - else **select** the best candidate (Decision 3): sort by
    `(status in {user_corrected, user_confirmed}, confidence, created_at)` desc, take first.
  - **format** the selected value per `spec.format` (§3.3); set `transformed`.
  - carry `profile_field_id`, `source_doc_id`, `high_stakes` (from spec), and the candidate's
    `status` (for the scorer's upstream-trust check) and raw confidence forward.

### 6.4 `agent/tools/confidence_scorer_tool.py` — provisional score
Per field (pure; no verification, no LLM):
- **confidence (Decision 9):**
  - missing (`value is None`) → `0.0`.
  - candidate `status ∈ {user_confirmed, user_corrected}` → `1.0`.
  - else → the candidate's grounded `confidence` (inherited verbatim; a transform does not
    change it — Decision 4/11).
- **band:** `≥ ocr_confidence_high (0.90)` → `high`; `≥ ocr_confidence_medium (0.70)` →
  `medium`; else `low`.

### 6.5 Review-flag computation (in `confidence_scorer_tool`)
Compute `flags` (all that apply), then derive `needs_review` + the single `review_reason`:

```
flags = {
  "missing":          None | "no_mapping" | "no_candidate",   # from lookup
  "high_stakes":      spec.high_stakes,                        # template (FR8)
  "unverified_source": candidate.status in {needs_confirmation, failed_validation},  # Decision 10
  "low_confidence":   confidence < confidence_threshold (0.90),
  "transformed":      transformed,     # recorded, NOT a review trigger (Decision 11)
}
needs_review = missing is not None or high_stakes or unverified_source or low_confidence
```

`review_reason` is the single most salient trigger, by **precedence**:
1. `no_mapping` / `no_candidate` (missing)
2. `high_stakes`
3. `unverified_source`
4. `low_confidence`

`transformed` is never a `review_reason` (Decision 11). A user-acted candidate → `confidence
1.0` → not `low_confidence`, and `status` not unverified → so a confirmed, non-high-stakes field
is **not** flagged; a high-stakes field (e.g. `date_of_birth`, `aadhaar_number`) is **always**
flagged via the template flag, exactly as FR8 requires — even when the underlying profile value
was user-confirmed, because the form context (money/legal/ID) demands the Phase-3 re-check.

### 6.6 `fill_form_task` (`workers/tasks.py`) — the pipeline
`fill_form_task(form_id)` (already stubbed, `bind=True, max_retries=3`). Mirrors
`ocr_extract_task`'s durability posture:

1. Load `Form`; guard non-`pending`/re-trigger. Set `status=processing`; commit.
2. `get_document(form.s3_key)` → bytes; `preprocess(...)` → images + `page_count` (reuse Phase 1
   `services/preprocessing.py`; PDF page cap enforced there).
3. **Build the decrypted `ProfileSnapshot`:** load the user's `Profile` + `ProfileField` rows,
   `decrypt_field(effective_value_encrypted, aad=build_aad(profile_id, field_name))` each into
   an in-memory `CandidateView` grouped by `field_name`. (No profile ⇒ empty snapshot.) Raw
   plaintext stays in-memory only.
4. `graph.invoke(initial_state, config={"configurable": {"snapshot", "images", "classifier":
   vision_llm.classify_form}})` → `state.fields`.
5. **type_mismatch** → `status=type_mismatch`, `detected_form_type`, safe
   `fill_error="declared=<x> detected=<y>"`; **write no form fields**; set `filled_at`; commit;
   return.
6. **Persist** each `FieldResult` (§6.7).
7. `status=filled`; set `detected_form_type`, `filled_at`, `updated_at`; commit. Log by
   `form_id`/counts only (no PII).
8. **Retries:** transient failures (Gemini 429/5xx/timeout, S3 blip, network) →
   `self.retry(countdown=backoff * 2**retries)` capped at `fill_max_retries`; on exhaustion or
   a terminal error (undecodable form) → `status=failed` + safe `fill_error`. **Never** put raw
   PII, raw model output, or the image in `fill_error`/logs. Idempotent (§6.7), so a retry can't
   duplicate fields.

### 6.7 Persisting a filled field
For each `FieldResult`, delete-then-insert on `(form_id, field_name)` for idempotency, then:
- filled: `value_encrypted = encrypt_field(value, aad=build_aad(form_id, field_name))`;
  `value_masked = mask_for(profile_key, value)` (masks only when `profile_key` is
  Aadhaar/PAN — keyed on the **canonical** key, not the form field name).
- missing: `value_encrypted=None`, `value_masked=None`.
- copy `profile_key`, `profile_field_id`, `source_doc_id`, `confidence`, `confidence_band`,
  `high_stakes`, `transformed`, `needs_review`, `review_reason`, `flags`; `reviewed=False`.

### 6.8 `services/ocr/vision_llm.py` — add `classify_form`
Add a form-classification entry point alongside Phase 1's `extract()` (same Gemini provider,
same `VisionExtractionError` for transient/terminal signaling):
`classify_form(images: list[bytes], known_types: list[str]) -> str` — returns one of
`known_types` or `"unknown"`. Strict output (enum-constrained JSON); the model is instructed to
answer `unknown` rather than guess when the form doesn't clearly match a known type.
> Implementation note: consult the project's `claude-api`/provider reference before writing the
> Gemini call (model id, image parts, structured output).

### 6.9 API surface (`api/routes/forms.py`, `schemas/form.py`)
All under `/api`, JWT-authed via `get_current_user`; error shape `{"detail", "code"}`. Every
`Form`/`FormField` row is scoped to the caller — cross-user access → **`404`** (no existence
leak), matching Phase 1.

| Method | Path | Body / params | Success | Key errors |
|---|---|---|---|---|
| POST | `/forms/upload` | multipart: `file`, `form_type` (registry key) | `202 {form_id, status:"pending"}` | `413 FILE_TOO_LARGE`; `415 UNSUPPORTED_TYPE`; `422` bad/unknown `form_type`; `503 ENQUEUE_FAILED` |
| GET | `/forms/{id}` | — | `200 FormOut` (status + fields once `filled`) | `404` |

**Upload flow:** validate size + content type (reuse Phase 1 limits) **before** buffering the
whole body where possible; reject an unknown `form_type` (`422`, enumerated from the registry);
`put_document(user_id, bytes, content_type)`; create `Form(pending)`; enqueue
`fill_form_task(form_id)`; return `202`. Enqueue failure → mark `Form` `failed` and return
`503` (jobs must not silently drop — NFR).

**`GET /forms/{id}` response (`FormOut`)** — PII-safe per field:
```jsonc
{
  "id": "...", "form_type": "income_certificate", "display_name": "Income Certificate",
  "detected_form_type": "income_certificate",
  "status": "filled", "fill_error": null, "page_count": 1,
  "created_at": "...", "filled_at": "...",
  "fields": [
    {
      "id": "...", "field_name": "date_of_birth", "profile_key": "dob",
      "display_value": "12/04/1998",            // masked for Aadhaar/PAN; null when missing
      "confidence": 0.95, "confidence_band": "high",
      "high_stakes": true, "transformed": true,
      "needs_review": true, "review_reason": "high_stakes", "reviewed": false,
      "source": { "profile_field_id": "...", "document_id": "...", "doc_type": "aadhaar" }
    },
    {
      "id": "...", "field_name": "annual_income", "profile_key": null,
      "display_value": null,
      "confidence": 0.0, "confidence_band": "low",
      "high_stakes": true, "transformed": false,
      "needs_review": true, "review_reason": "no_mapping", "reviewed": false,
      "source": { "profile_field_id": null, "document_id": null, "doc_type": null }
    }
  ]
}
```
`fields` is empty until `status=filled`. **Never** put a full Aadhaar/PAN in any response, log,
or error — `display_value` is the masked form for those `profile_key`s (reuse Phase 1 masking).

---

## 7. Frontend (`frontend/src`)

Builds on Phase 0's `AuthContext`/`api/client.ts`/protected shell and Phase 1's `ConfidenceField`
and upload-with-poll pattern.

- **`api/client.ts`** — add `uploadForm(file, formType)` → `{form_id, status}`;
  `getForm(id)` → `FormDraft`.
- **`types.ts`** — `FormType`, `FormStatus`, `FormFieldDraft`, `FormDraft` (mirror `FormOut`).
- **`pages/FormFill.tsx`** — a form-type selector (income certificate / scholarship application),
  drag-drop **and** mobile camera capture
  (`<input type="file" accept="image/*,application/pdf" capture="environment">`), client-side
  size/type pre-check, then **poll** `getForm(id)` (2 s, backing off, capped) until
  `filled`/`failed`/`type_mismatch`. On `type_mismatch`, show "this looks like a `<detected>` —
  re-select or re-upload".
- **Draft view** (same page once `filled`, or `pages/FormDraft.tsx`) — **read-only** in Phase 2:
  each field shows its `display_value` (masked where applicable), a **green/yellow/red**
  `ConfidenceField` band, a **review badge** when `needs_review` (with the `review_reason`), and
  its source doc-type. **No approve/edit/download controls** — those arrive in Phase 3. A clear
  banner states the draft is not final and not submitted.
- **Routing/nav:** add a "Fill a form" route to the protected shell; link from the dashboard.
- Minimal, unstyled-but-usable (consistent with Phases 0–1).

---

## 8. Security & edge cases (must-handle)
- **No auto-submit** (FR7): Phase 2 produces only an in-app draft; there is no submit path, and
  none is ever built.
- **No raw PII in logs** (CLAUDE.md): never log field values, form values, snippets,
  Aadhaar/PAN, images, or raw model output. Log by `form_id`/`form_field_id`/counts only.
  `fill_error` is a fixed safe string, never interpolated PII.
- **Field-level encryption at rest** (FR2): every filled `FormField.value_encrypted` is
  AES-256-GCM with AAD `build_aad(form_id, field_name)`, binding the ciphertext to its row.
- **Sensitive-ID masking is total:** a full Aadhaar/PAN exists only encrypted at rest and
  in-memory during the fill; API responses, UI, and logs see the masked form only (keyed on the
  canonical `profile_key`).
- **Ownership scoping:** all form reads filter by `user_id`; cross-user → `404`.
- **Upload abuse:** enforce `max_upload_bytes` + content-type allowlist + PDF page cap (reuse
  Phase 1); reject oversized before buffering.
- **`type_mismatch` safety:** a form the LLM confidently recognizes as a *different known type*
  never gets silently filled against the wrong template; an `unknown` classification defers to
  the user's declared type (does not block a legitimate fill).
- **Confidence is provisional & inherited, never fresh self-report:** Phase 2 does not re-verify
  the mapped/formatted value; the score is inherited from the profile candidate and the draft is
  explicitly not a final trust signal (Phase 3's `document_verification_tool` closes that loop).
  An unresolved upstream candidate propagates as `unverified_source` (Decision 10).
- **Auditability** (NFR): every filled field carries `profile_field_id` + `source_doc_id`
  provenance and the `confidence`/`flags` that justified it — traceable back to the exact profile
  candidate and source document.
- **Third-party processing:** the blank form image is sent to Google Gemini for classification —
  same processing disclosure as Phase 1; no image logged locally.
- **Job durability** (NFR): enqueue failures surface (`503` + `Form` `failed`); transient fill
  errors retry with backoff; the task is idempotent (delete-then-insert), so a retry can't
  duplicate fields; the form always reaches a terminal status (`filled`/`failed`/`type_mismatch`).
- **Deferred hardening (noted, not built):** per-user upload rate limiting, malware scan of
  uploads, S3 SSE-KMS, a consent audit trail for third-party processing.

---

## 9. Metrics seam (full instrumentation is Phase 6)
Phase 2 makes fill latency + the auto-fill/flag split **recoverable**, not yet dashboarded:
`Form.created_at → Form.filled_at` = upload→draft-ready latency; the `needs_review`/
`confidence_band` distribution across `FormField` rows gives an early read on the "% fields
auto-fillable at high confidence" metric (PRD §9). No `metrics/` code this phase.

---

## 10. Testing (`backend/tests`, `frontend`)

**Backend (pytest):**
- **template registry:** loads both templates; rejects a bad `profile_key`, a bad `format`
  grammar, a missing required key; `known_types()` matches the files.
- **`profile_lookup_tool` (pure):** deterministic map by `profile_key`; **candidate selection**
  order (user-acted > confidence > recency) with two candidates for one key; `no_mapping` vs
  `no_candidate`; each `format` transform (date reformat, upper, single_line) sets `transformed`
  correctly; a bad date is passed through and left for scoring.
- **`confidence_scorer_tool` (pure):** confidence rules (missing→0, user-acted→1.0, else
  inherit); banding thresholds; the `flags`/`needs_review` truth table incl. the `review_reason`
  **precedence**; `transformed` never flags; `unverified_source` propagation; a confirmed
  non-high-stakes field is **not** flagged while a high-stakes field **is**.
- **graph (`build_graph`) with fakes:** inject a fake snapshot + stub classifier — happy path
  produces one `FieldResult` per template field; `type_mismatch` short-circuits to no fields;
  `unknown` classification proceeds on the declared type.
- **`fill_form_task`:** mocked storage + graph → writes `FormField`s, `status=filled`,
  `filled_at` set; transient error retries; terminal error → `failed` + safe `fill_error`;
  `type_mismatch` writes **no** fields; re-run is idempotent (no duplicate `form_fields`).
- **encryption/masking:** a filled Aadhaar `FormField` stores ciphertext + a masked
  `value_masked`; AAD is `(form_id, field_name)`.
- **API:** upload validates size/type + enqueues (mock Celery) + rejects unknown `form_type`
  (`422`); `GET /forms/{id}` scoped to owner (cross-user → `404`); the response **never** returns
  a full Aadhaar/PAN; missing fields return `display_value: null` with the right `review_reason`.
- **provenance:** a filled field's `profile_field_id`/`source_doc_id` point at the selected
  candidate's row/document.

**Frontend (vitest, light):** FormFill polls status and renders terminal states incl.
`type_mismatch`; the draft view renders correct green/yellow/red bands, masks sensitive values,
shows review badges with reasons, and exposes **no** approve/edit/download control.

---

## 11. File-by-file change list

**Backend — implement (currently stubs):**
`agent/graph.py` (`build_graph`), `agent/state.py` (extend `AgentState`/`FieldResult`),
`agent/tools/form_schema_tool.py` (registry + known-template branch),
`agent/tools/profile_lookup_tool.py`, `agent/tools/confidence_scorer_tool.py`,
`models/form.py` (`Form` + `FormField`), `api/routes/forms.py`
(`POST /upload`, `GET /{id}`), `workers/tasks.py` (`fill_form_task`),
`services/ocr/vision_llm.py` (add `classify_form`).

**Backend — new:**
`schemas/form.py` (`FormOut`, `FormFieldOut`, upload contracts),
`db/migrations/versions/0003_forms.py`, `templates/scholarship_application.json`.

**Backend — edit:**
`templates/income_certificate.json` (add per-field `format`),
`db/base.py` (import `Form`, `FormField`), `config.py` (optional `FILL_*` knobs),
`pyproject.toml` (add `langgraph`), `main.py` (load the template registry at startup, fail-fast
on a bad template).

**Frontend — new/implement:**
`pages/FormFill.tsx` (+ optional `pages/FormDraft.tsx`), `api/client.ts` (add methods),
`types.ts` (add form types), route wiring in `App.tsx`.

**Infra / config:** `.env.example` (optional `FILL_*` knobs; no new required secrets). No new
compose services (MinIO/Redis/Gemini already wired from Phases 0–1).

**Untouched this phase (stay stubs):** `agent/tools/document_verification_tool.py`,
`services/form_renderer.py`, `services/ocr/tesseract.py`, `metrics/`, the history router, and
the forms `review`/`download` routes. `form_schema_tool`'s inference branch stays a stub
(Phase 4). `DELETE /api/profile` stays a stub (Phase 5).

---

## 12. Non-goals reminder (don't drift)
No document verification of *mapped* values, no HITL review UI, no approve/correct, no download
gating, no form rendering/PDF, no schema inference, no LLM semantic matching, no metrics
dashboards, and no auto-submit in Phase 2 — those are Phases 3–6 (or never, for auto-submit).
Phase 2 exists solely to turn an uploaded **known** blank form into a **provisional draft**:
every field mapped deterministically from the encrypted profile, formatted, scored with an
**inherited** (not freshly verified) confidence, and flagged for the review step that Phase 3
will enforce. The score here is the *seed* of, not a substitute for, Phase 3's
`document_verification_tool`.

---

## 13. Acceptance checklist (Done-When, enumerated)
1. Upload a blank income-certificate image/PDF (drag-drop or camera) with
   `form_type=income_certificate` → `202` + a `form_id`.
2. Poll `GET /forms/{id}` → status transitions `pending`→`processing`→`filled`.
3. The `filled` draft lists **every** template field, each with a provisional `confidence` +
   band, `high_stakes`, `needs_review` + `review_reason`, and `source` provenance
   (`profile_field_id`/`document_id`/`doc_type`).
4. A field filled from a user-confirmed profile candidate shows `confidence 1.0`; a field from
   an unconfirmed candidate propagates `needs_review` with reason `unverified_source`;
   high-stakes fields (`date_of_birth`, `aadhaar_number`) are flagged regardless of score.
5. `date_of_birth` is reformatted per the template (`YYYY-MM-DD → DD/MM/YYYY`) with
   `transformed=true`, and the reformat alone does **not** set `needs_review`.
6. `annual_income` (no `profile_key`) and any mapped-but-absent field come back with
   `display_value: null`, `confidence 0`, and reason `no_mapping` / `no_candidate` respectively.
7. Aadhaar in the draft is **masked** in the response; no log line or API response contains a
   full Aadhaar/PAN.
8. Uploading a scholarship form labeled `income_certificate` (LLM confidently detects the other
   known type) → `status=type_mismatch` and **no** form fields written; an unrecognizable
   upload labeled `income_certificate` still fills on the declared type.
9. At-rest inspection: `form_fields.value_encrypted` is ciphertext (not readable plaintext).
10. A transient fill failure retries and can still succeed; an undecodable form ends `failed`
    with a safe reason; a re-run produces no duplicate `form_fields`.
11. The draft view is **read-only** — no approve/edit/download control exists yet (Phase 3).
12. `ruff`/`mypy` clean; new backend tests + light frontend tests green (per
    memory/dev-environment).
```
