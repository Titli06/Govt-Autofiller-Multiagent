# SPEC — Phase 1: Profile Ingestion from ID Documents

Scope-locked spec for **Phase 1 only** of [PLAN.md](PLAN.md). Ships one end-to-end vertical
slice — DB → backend → async worker → frontend — delivering the first real product feature:
a user uploads an ID document, a vision-LLM extracts it into typed fields, those fields are
stored **field-level encrypted** with source-document provenance, low-confidence/high-stakes
fields are routed to a lightweight confirmation step, and the user can view their profile.

> **Authority:** [PLAN.md](PLAN.md) Phase 1 + [govform-autofiller-prd.md](govform-autofiller-prd.md)
> UC1, FR1/FR2/FR12, §5.1–5.3 (vision-LLM primary, LLM-schema extraction), §6 (data flow 1),
> §8 (security/privacy NFRs), §10 (hallucination + PII risks), and [CLAUDE.md](CLAUDE.md)
> (confidence grounded in source-doc match; PII encrypted field-level; no raw-PII logging;
> every value auditable to its source doc). Where PLAN and PRD are silent, the decisions below
> were made in the Phase 1 build interview and are **binding for this phase**.
>
> Builds on Phase 0 ([SPEC.md](SPEC.md)): every Document/Profile hangs off the authenticated
> `User`. Reuses the sync SQLAlchemy 2.0 + `get_current_user` + Celery seams established there.
>
> **Post-implementation deviation:** the vision-LLM was swapped from Anthropic Claude (as
> named below and in the PRD) to **Google Gemini** (`google-genai`, `services/ocr/vision_llm.py`)
> after this spec was written. The rest of this document's references to "Anthropic"/tool-use
> describe the original design intent; the module's public interface (`extract()`,
> `RawExtraction`, `VisionExtractionError`) was kept unchanged, so nothing downstream
> (`extraction.py`'s grounding, `ocr_extract_task`) needed to change. See
> memory/phase1-decisions.md for the rationale.

---

## 1. Objectives & Done-When

**Done when:** an authenticated user uploads an Aadhaar or PAN image (drag-drop or mobile
camera) → the document is stored in S3/MinIO and an async OCR/extraction job runs → the user
polls status → on success they see their extracted profile fields, each showing its **grounded
confidence**, its **source document** (provenance), and — for low-confidence or high-stakes
fields — a **required confirm/correct** control; sensitive ID numbers (Aadhaar/PAN) are stored
encrypted and **only ever displayed masked**; nothing raw-PII is logged.

Acceptance is enumerated in §13.

### In scope
- **DB:** `Document` (metadata + `s3_key` + `ocr_status`), `Profile` (one per user),
  `ProfileField` (**multi-candidate**, field-level-encrypted value, source-doc provenance,
  grounded confidence, confirmation state). First Phase-1 Alembic migration.
- **Encryption:** `core/encryption.py` — AES-256-GCM field-level encrypt/decrypt + mask helpers.
- **Storage:** `services/storage.py` — S3/MinIO put/get/delete + private-bucket ensure.
- **OCR/extraction:** `services/ocr/vision_llm.py` (Anthropic vision, primary) +
  `services/extraction.py` (strict-schema JSON extraction **with per-field source snippet**) +
  a **deterministic grounding** layer that computes confidence from snippet-match + format
  validators (not LLM self-report).
- **Worker:** implement `ocr_extract_task` (bounded retry, partial success, failed state).
- **API:** `POST /api/documents/upload`, `GET /api/documents/{id}/status`,
  `GET /api/documents/{id}/file` (owner-only source-doc stream), `GET /api/profile`,
  `POST /api/profile/fields/{id}/confirm`, `POST /api/profile/fields/{id}/correct`.
- **Frontend:** Upload page (drag-drop + camera, doc-type select, job-status polling) and a
  Profile view (fields grouped by name, green/yellow/red confidence bands, confirm/edit on
  flagged fields, source-doc side-by-side).
- **Doc types this phase:** **Aadhaar + PAN** only.

### Out of scope (defer to later phases)
- **Marksheet & address-proof** extraction schemas — deferred (messier layouts); enum/UI leave
  room but no extraction schema is built now.
- **Tesseract** first-pass/cost path (`services/ocr/tesseract.py` stays a stub) — vision-LLM
  handles all docs in Phase 1; Tesseract is a later cost optimization.
- **Form** upload / schema ID / mapping / fill / agent graph — Phase 2+.
- **`document_verification_tool`** (fresh cross-check of a *mapped/formatted* form value) —
  Phase 3. Phase 1's grounding validates the *extracted* value against its own source snippet;
  it is the seed of, not a replacement for, Phase 3 verification.
- **Profile deletion / cascade purge** (`DELETE /api/profile`) — Phase 5. FKs are declared
  `ON DELETE CASCADE` now so the purge is a one-liner later, but the endpoint stays a stub.
- **Full metrics instrumentation** — Phase 6. Phase 1 only records ingestion timestamps
  (`created_at` → `extracted_at`) so latency is *recoverable*, not yet dashboarded.
- Key-management service / envelope encryption with a real KMS — Phase 1 uses a single local
  key with a version byte so rotation is a later, non-breaking change.

---

## 2. Decisions carried from the interview (binding for Phase 1)

| # | Area | Decision |
|---|---|---|
| 1 | **Profile field model** | **Multi-candidate per field.** The same logical field extracted from different docs is stored as **separate `ProfileField` rows** keyed by `(field_name, source_doc)`, each with its own confidence + provenance. No overwrite; Phase 2 picks the best candidate, Phase 3 verifies against the correct source. Preserves the full audit trail. |
| 2 | **Profile HITL** | **Hybrid — confirm low-confidence + high-stakes only.** High-confidence, non-high-stakes fields are auto-accepted (`confirmed`). Fields below `CONFIDENCE_THRESHOLD` **or** high-stakes (Aadhaar/PAN number, DOB) land in `needs_confirmation` and must be resolved by the user. Mirrors the form-fill review model; low friction. |
| 3 | **Sensitive IDs** | **Store full, encrypted; display masked.** Full Aadhaar/PAN kept field-level-encrypted at rest (forms need the complete number); UI **and logs** only ever see the masked form (`XXXX XXXX 1234`). |
| 4 | **Doc-type scope** | **Aadhaar + PAN** get real extraction schemas this phase. Marksheet/address-proof deferred. |
| 5 | **Confidence grounding** | **Snippet + deterministic recheck.** The LLM returns each value **plus the verbatim source snippet** it read it from. A deterministic layer validates snippet-contains-value + format rules (PAN regex, Aadhaar 12-digit + Verhoeff, DOB parse). **That** sets confidence; the model's self-reported number is only a tiebreaker, never the sole signal. |
| 6 | **Doc-type routing** | **User selects, system confirms.** User declares the type at upload; the vision-LLM also classifies. On disagreement the doc is **flagged** (`ocr_status = type_mismatch`, no profile written) rather than silently extracted against the wrong schema. |
| 7 | **Encryption** | **AES-256-GCM, random 12-byte nonce, key-id/version byte prefix.** Authenticated (tamper-evident); randomized (not ciphertext-searchable — fine, we look up by plaintext `field_name`); version byte enables later key rotation with no schema change. Binds ciphertext to its row via AAD. |
| 8 | **Failure handling** | **Bounded retry + partial + failed state.** Celery auto-retries *transient* errors (API 429/5xx/timeout, S3 blips) with capped exponential backoff. Extract whatever is readable and flag the rest missing (partial is still useful). Unrecoverable → `ocr_status = failed` with a **safe, non-PII reason**; user can re-trigger/re-upload. |
| 9 | **Upload formats** | **Images + PDF, HEIC converted, ~10 MB cap.** Accept JPG/PNG/WEBP/HEIC + PDF (multi-page: all pages sent to the model). Server-side HEIC→JPEG, orientation-normalize + downscale before OCR. |
| 10 | **Raw-doc retention** | **Retain in a private S3 bucket; purged in Phase 5.** Required for Phase 3 source-doc verification, provenance, and side-by-side review. Deletion is the Phase 5 cascade's job. |
| 11 | **Spec location** | This file — `SPEC-PHASE1.md`. `SPEC.md` stays the Phase 0 record. |

### Default implementation choices (not interviewed; set here)
- **Canonical field vocabulary** (internal `field_name` values) matches the template
  `profile_key`s already in `templates/income_certificate.json`: `full_name`, `father_name`,
  `dob`, `gender`, `address`, `aadhaar_number`, `pan_number`. (See §3.)
- **High-stakes profile fields:** `aadhaar_number`, `pan_number`, `dob` — always
  `needs_confirmation` regardless of confidence (mirrors FR8's ID/date category).
- **One `Profile` per user**, created lazily on the first successful extraction.
- **Source-doc access** is via an **owner-authenticated API stream** (`/documents/{id}/file`),
  not a public/presigned URL — keeps auth in one place. (Presigned URLs noted as a later option.)
- **Task idempotency:** re-running `ocr_extract_task` for a document first deletes that
  document's prior candidate rows, then re-writes — a re-trigger is clean and repeatable.
- **DB access / IDs / timestamps:** same as Phase 0 — sync SQLAlchemy 2.0, `psycopg` v3,
  UUIDv4 PKs, `TIMESTAMP WITH TIME ZONE` UTC with `now()` server defaults.

---

## 3. Canonical profile schema & per-doc extraction schemas

### 3.1 Canonical field vocabulary
All extracted values normalize to these internal `field_name`s (the vocabulary Phase 2's
`profile_lookup_tool` maps form fields onto):

| `field_name` | Type / normalized form | Sensitive? | High-stakes? |
|---|---|---|---|
| `full_name` | string, as printed | PII | no |
| `father_name` | string | PII | no |
| `dob` | ISO `YYYY-MM-DD` | PII | **yes** |
| `gender` | `Male` \| `Female` \| `Other` | PII | no |
| `address` | string (multi-line preserved) | PII | no |
| `aadhaar_number` | 12 digits, no spaces | **high** — mask always | **yes** |
| `pan_number` | `^[A-Z]{5}[0-9]{4}[A-Z]$` | **high** — mask always | **yes** |

"Sensitive: high" ⇒ stored encrypted **and** never returned/logged un-masked. All fields are
PII and encrypted at rest; the extra rule for high-sensitivity is the masking in transit/logs.

### 3.2 Per-doc-type target schemas
The LLM is constrained to exactly these fields for the declared doc type (strict schema —
unknown fields rejected; absent fields returned as `present: false`, never invented):

- **`aadhaar`** → `full_name`, `dob`, `gender`, `aadhaar_number`, `address`
- **`pan`** → `full_name`, `father_name`, `dob`, `pan_number`

`full_name` and `dob` appear on both → the multi-candidate model (Decision 1) is what keeps
each doc's version distinct instead of clobbering.

### 3.3 Deterministic format validators (grounding)
Per field type, applied **after** extraction to set grounded confidence (§6.3):

| Field | Validator |
|---|---|
| `aadhaar_number` | strip spaces → exactly 12 digits → **Verhoeff checksum** valid |
| `pan_number` | uppercase → matches `^[A-Z]{5}[0-9]{4}[A-Z]$` |
| `dob` | parses to a real date; plausible range (e.g. 1900 ≤ year ≤ current); normalize to ISO |
| `gender` | maps into `{Male, Female, Other}` (accept `M`/`F`/`O`, Hindi variants) |
| `full_name`, `father_name`, `address` | free text — no format rule; grounded on snippet-contains + normalization distance only |

---

## 4. Data Model

Three tables (+ FKs into Phase 0's `users`). One Phase-1 Alembic migration
(`0002_profile_ingestion`) creates all three; `db/base.py` must import the new models so
autogenerate sees them.

### 4.1 `documents` (`models/document.py`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → users.id | `ON DELETE CASCADE`, indexed |
| `declared_doc_type` | text, not null | user's choice: `aadhaar` \| `pan` |
| `detected_doc_type` | text, nullable | vision-LLM classification result |
| `s3_key` | text, not null | object key in the private bucket |
| `content_type` | text | original upload MIME (post-HEIC-convert value stored too if changed) |
| `byte_size` | int | |
| `page_count` | int, nullable | for PDFs |
| `ocr_status` | text, not null | enum: `pending` → `processing` → (`extracted` \| `partial` \| `failed` \| `type_mismatch`) |
| `ocr_error` | text, nullable | **safe, non-PII** reason on failure/mismatch |
| `extracted_at` | timestamptz, nullable | set when OCR finishes (latency = `extracted_at − created_at`) |
| `created_at` / `updated_at` | timestamptz | server default `now()` / on-update |

### 4.2 `profiles` (`models/profile.py`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → users.id | `ON DELETE CASCADE`, **UNIQUE** (one profile per user) |
| `created_at` / `updated_at` | timestamptz | |

### 4.3 `profile_fields` (`models/profile.py`)
One row per **candidate** — i.e. per `(field, source document)`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `profile_id` | UUID FK → profiles.id | `ON DELETE CASCADE`, indexed |
| `source_doc_id` | UUID FK → documents.id | `ON DELETE CASCADE` — **provenance** |
| `field_name` | text, not null | canonical vocab (§3.1) |
| `value_encrypted` | bytea, not null | AES-256-GCM of the **extracted** value (immutable audit of what OCR produced) |
| `corrected_value_encrypted` | bytea, nullable | set if the user edits; **effective value** = corrected if present else extracted |
| `value_masked` | text, nullable | display-safe masked form; **required** for high-sensitivity fields (Aadhaar/PAN), null otherwise |
| `source_snippet_encrypted` | bytea, nullable | verbatim snippet the value was read from (encrypted — it contains the value) |
| `confidence` | float, not null | grounded confidence in `[0,1]` (§6.3) |
| `confidence_band` | text, not null | `high` \| `medium` \| `low` (derived; stored for query/metrics) |
| `high_stakes` | bool, not null | drives mandatory confirmation |
| `status` | text, not null | enum: `confirmed` \| `needs_confirmation` \| `user_confirmed` \| `user_corrected` \| `failed_validation` |
| `validators` | jsonb, nullable | `{snippet_contains: bool, format_valid: bool, normalized: bool}` — audit of why the score landed |
| `created_at` / `updated_at` | timestamptz | |

**Constraints & indexes:**
- `UNIQUE (profile_id, field_name, source_doc_id)` — exactly one candidate per field per source
  doc; a re-run replaces via delete-then-insert.
- index on `(profile_id, field_name)` — grouping for the profile view.

**Status semantics:** `confirmed` = auto-accepted (high conf, not high-stakes).
`needs_confirmation` = below threshold **or** high-stakes → blocks nothing in Phase 1 but is
surfaced as "action needed" (it *does* block form-fill trust in later phases).
`user_confirmed`/`user_corrected` = user acted. `failed_validation` = format check failed
(e.g. 11-digit "Aadhaar") → always `needs_confirmation`-equivalent, shown red.

---

## 5. Config & env additions

`config.py` gains (mirror in `.env.example`):

```
# Field-level PII encryption — REQUIRED in Phase 1.
# pii_encryption_key must be base64 of exactly 32 bytes (AES-256). Validated at startup.
pii_encryption_key: str            # already present; now load-validated

# Uploads
max_upload_bytes: int = 10_485_760           # 10 MiB
allowed_upload_content_types: list[str] = [
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"
]

# OCR / extraction worker
ocr_max_retries: int = 3
ocr_retry_backoff_seconds: int = 5           # base for exponential backoff
ocr_confidence_high: float = 0.90            # ≥ → band=high (aligns with confidence_threshold)
ocr_confidence_medium: float = 0.70          # ≥ → band=medium; below → low

# S3 already present (s3_endpoint_url/keys/bucket/region); vision_model already present.
```

**`.env.example`:** add `MAX_UPLOAD_BYTES`, `OCR_MAX_RETRIES`, `OCR_RETRY_BACKOFF_SECONDS`;
add a comment that `PII_ENCRYPTION_KEY` is now **required** and must be
`base64(32 bytes)` (e.g. `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`).
`ANTHROPIC_API_KEY` becomes required for extraction to run.

**Dependencies (`backend/pyproject.toml`):** add **`pillow-heif`** (HEIC→JPEG). `pillow`,
`pypdf`, `boto3`, `anthropic`, `cryptography`, `python-multipart` are already present.
Dev: add **`moto`** (mock S3 for storage tests). `pytesseract` stays installed but unused.

---

## 6. Backend components

### 6.1 `core/encryption.py` (AES-256-GCM, key-versioned)
Pure functions; no DB access.

- **Key load:** at import, base64-decode `settings.pii_encryption_key` → assert exactly 32
  bytes, else raise a clear startup error. Held in a version-indexed keyring `{0x01: key}`.
- `encrypt_field(plaintext: str, aad: bytes | None = None) -> bytes`
  → `b"\x01" + nonce(12B) + AESGCM(key).encrypt(nonce, plaintext.utf-8, aad)`
  (ciphertext already includes the 16-byte GCM tag). Random nonce per call.
- `decrypt_field(blob: bytes, aad: bytes | None = None) -> str`
  → read version byte → select key → split nonce → `AESGCM.decrypt(...)` → utf-8. Raises on
  tamper (GCM auth failure) or wrong AAD.
- **AAD binding:** callers pass `aad = f"{profile_id}:{field_name}".encode()` so a ciphertext
  can't be silently copied to another field/row and still decrypt. (Recorded in §6.4.)
- **Masking helpers:** `mask_aadhaar(v) -> "XXXX XXXX 1234"` (last 4),
  `mask_pan(v) -> "XXXXXX234F"` (last 4), `mask_for(field_name, value)` dispatcher.
- **Rotation seam:** the version byte selects the key; adding `0x02` later + a background
  re-encrypt is non-breaking. Phase 1 ships one key.

### 6.2 `services/storage.py` (S3 / MinIO)
`boto3` client from `settings` (endpoint_url, access/secret, region). All objects go in one
**private** bucket (`s3_bucket`).

- `ensure_bucket()` — idempotent create-if-missing (MinIO dev convenience); call at startup.
- `put_document(user_id, data: bytes, content_type) -> str` — key
  `documents/{user_id}/{uuid4}{ext}`, `private` ACL, returns the key.
- `get_document(key) -> bytes` — used by the worker and the file-stream endpoint.
- `delete_document(key)` — present now; the Phase 5 cascade calls it.
- Transient boto errors are retryable and surface as such to the worker (§6.5).

### 6.3 OCR + extraction + grounding

**`services/ocr/vision_llm.py`** — the vision-LLM call (Anthropic, `settings.vision_model`,
currently `claude-opus-4-8`). Contract:
`extract(images: list[bytes], declared_doc_type: str) -> RawExtraction` where `RawExtraction`
carries `detected_doc_type` and, per schema field (§3.2), `{value, source_snippet,
self_confidence, present}`. Implementation uses the Anthropic Messages API with image content
blocks + **tool-use / strict JSON-schema output** derived from the doc type; the model is
instructed to (a) classify the document, (b) return each field **with the verbatim snippet it
read the value from**, (c) mark absent fields `present: false` and **never invent** values.
> Implementation note: consult the project's `claude-api` reference before writing the
> Anthropic call (model id, image blocks, tool-use, token limits).

**`services/extraction.py`** — orchestrates grounding. Contract:
`extract_profile_fields(images, declared_doc_type) -> ExtractionResult`:
1. call `vision_llm.extract(...)`.
2. **classification gate:** if `detected_doc_type` is present and disagrees with
   `declared_doc_type` → return an `ExtractionResult` flagged `type_mismatch` (no fields).
3. for each present field, run the **deterministic grounding**:
   - `snippet_contains` — normalized `value` is a substring of normalized `source_snippet`.
   - `format_valid` — the §3.3 validator for that field (n/a for free-text → treated as pass).
   - `normalized` — whether a normalization was applied (e.g. `12/04/1998` → `1998-04-12`).
   - **grounded confidence** (deterministic; self-report only breaks ties within a band):
     - snippet-contains **and** format-valid (typed field) → **0.95–0.97**
     - snippet-contains, free-text field (name/address) → **0.80–0.88** (band = medium unless self-report high and snippet exact → up to high)
     - snippet-contains but a **normalization** was needed → cap at **0.85**
     - value present but **snippet missing / value not in snippet** → **≤ 0.55** (band = low)
     - **format-invalid** typed field (e.g. bad Aadhaar checksum) → **≤ 0.40**, `status=failed_validation`
     - field **absent** (`present:false`) → no candidate row written; recorded as a *missing* field for partial-status accounting
   - **band:** `≥ ocr_confidence_high` → `high`; `≥ ocr_confidence_medium` → `medium`; else `low`.
4. return typed fields + the list of missing required fields.

### 6.4 Persisting a candidate (worker → DB)
For each grounded field: encrypt the value with `encrypt_field(value, aad=f"{profile_id}:{field_name}")`,
compute `value_masked` for high-sensitivity fields, encrypt `source_snippet`, set `high_stakes`
(from §3.1), and set `status`:
- `high_stakes` **or** `confidence < confidence_threshold` **or** `failed_validation`
  → `needs_confirmation` (`failed_validation` keeps its own status but is treated as needs-action).
- else → `confirmed`.

Write via delete-then-insert on `(profile_id, field_name, source_doc_id)` for idempotency.

### 6.5 `ocr_extract_task` (`workers/tasks.py`) — the pipeline
`ocr_extract_task(document_id)` (already stubbed, `bind=True, max_retries=3`):

1. Load `Document`; if not `pending`/re-trigger → guard. Set `ocr_status=processing`.
2. `get_document(s3_key)` → bytes.
3. **Preprocess:** HEIC/HEIF → JPEG (`pillow-heif`); PDF → per-page images (`pypdf`+`pillow`),
   capture `page_count`; normalize orientation, downscale oversized images.
4. `extract_profile_fields(images, declared_doc_type)`.
5. **type_mismatch** → set `ocr_status=type_mismatch`, `detected_doc_type`, safe
   `ocr_error="declared=<x> detected=<y>"`; **write no profile fields**; return.
6. Upsert the user's `Profile` (create if first).
7. Persist each candidate (§6.4).
8. **Status:** all schema fields present → `extracted`; some present, some missing → `partial`;
   none extractable / unreadable → `failed`. Set `extracted_at`, `updated_at`.
9. **Retries:** wrap external calls; **transient** (Anthropic 429/5xx/timeout, S3 transient,
   network) → `self.retry(countdown=ocr_retry_backoff_seconds * 2**self.request.retries)`,
   capped at `ocr_max_retries`; on exhaustion or **terminal** error (corrupt/unreadable image,
   unsupported content) → `ocr_status=failed` + safe `ocr_error`. **Never** put raw PII, raw
   model output, or the image in `ocr_error` or logs.

### 6.6 API surface (`api/routes/documents.py`, `api/routes/profile.py`, `api/deps.py`)
All under `/api`, JWT-authed via Phase 0's `get_current_user`; error shape
`{"detail", "code"}` as in Phase 0. **Every** document/profile row is scoped to the calling
user — cross-user access → `404` (not `403`, to avoid existence leaks).

| Method | Path | Body / params | Success | Key errors |
|---|---|---|---|---|
| POST | `/documents/upload` | multipart: `file`, `doc_type` (`aadhaar`\|`pan`) | `202 {document_id, ocr_status:"pending"}` | `413 FILE_TOO_LARGE`; `415 UNSUPPORTED_TYPE`; `422` bad `doc_type` |
| GET | `/documents/{id}/status` | — | `200 {id, declared_doc_type, detected_doc_type, ocr_status, ocr_error, page_count, created_at, extracted_at}` | `404` |
| GET | `/documents/{id}/file` | — | `200` streams raw bytes (owner only; for review side-by-side) | `404` |
| GET | `/profile` | — | `200 {fields:[…grouped candidates…]}` (see below) | `200 {fields:[]}` if none |
| POST | `/profile/fields/{id}/confirm` | — | `200` field → `user_confirmed` | `404` |
| POST | `/profile/fields/{id}/correct` | `{value}` | `200` field → `user_corrected`, `confidence=1.0` | `404`; `422` value fails that field's format validator |

**Upload flow:** validate size (`max_upload_bytes`) and content type (`allowed_upload_content_types`)
**before** reading the whole body where possible; `put_document`; create `Document(pending)`;
enqueue `ocr_extract_task(document_id)`; return `202`. Enqueue failure → `503` and mark doc
`failed` (job must not be silently dropped — NFR).

**`GET /profile` response** — per candidate, PII-safe:
```
{
  "id": "...", "field_name": "aadhaar_number",
  "display_value": "XXXX XXXX 1234",     // masked for high-sensitivity; decrypted plaintext otherwise (owner only)
  "confidence": 0.96, "confidence_band": "high",
  "high_stakes": true, "status": "needs_confirmation",
  "source": { "document_id": "...", "doc_type": "aadhaar" }
}
```
The frontend groups by `field_name` to render candidates side by side. **Never** put a full
Aadhaar/PAN in any response, log line, or error.

**`/correct`:** re-validate the submitted value against that field's format validator (§3.3);
on pass, store `corrected_value_encrypted` (+ recompute mask), `status=user_corrected`,
`confidence=1.0`. Original extracted `value_encrypted` is retained for audit.

---

## 7. Frontend (`frontend/src`)

Builds on Phase 0's `AuthContext` + `api/client.ts` + protected shell (`/upload` was an empty
placeholder; now real).

- **`api/client.ts`** — add typed methods: `uploadDocument(file, docType)`,
  `getDocumentStatus(id)`, `getProfile()`, `confirmField(id)`, `correctField(id, value)`,
  and a `documentFileUrl(id)` helper (points at `/api/documents/{id}/file`, cookie-authed).
- **`types.ts`** — `DocType`, `DocumentStatus`, `OcrStatus`, `ProfileField`, `ConfidenceBand`.
- **`pages/Upload.tsx`** — drag-drop **and** mobile camera capture
  (`<input type="file" accept="image/*,application/pdf" capture="environment">`), a doc-type
  selector (Aadhaar/PAN), client-side size/type pre-check, then **poll**
  `getDocumentStatus` (e.g. every 2 s, backing off, with a cap) until
  `extracted`/`partial`/`failed`/`type_mismatch`; show progress and a clear terminal message.
  On `type_mismatch`, prompt "we think this is a `<detected>` — re-select or re-upload".
- **Profile view** (`pages/Profile.tsx` or the dashboard) — fields grouped by `field_name`;
  each candidate shows its value (masked where applicable), a **green/yellow/red** band
  (high/medium/low), and its source doc. `needs_confirmation`/`failed_validation` fields show
  **Confirm** and **Edit** controls and the **source document side-by-side**
  (`documentFileUrl`). Confirm/correct call the API and update in place.
- **Routing/nav:** add a Profile route to the protected shell; link it from the dashboard.
- Minimal, unstyled-but-usable (consistent with Phase 0). `ConfidenceField` component
  (green/yellow/red) is introduced here and reused by the Phase 3 review UI.

---

## 8. Security & edge cases (must-handle)

- **No raw PII in logs** (CLAUDE.md): never log field values, snippets, Aadhaar/PAN, images,
  or raw model output. Log by `document_id` / `profile_field_id` only. `ocr_error` is a fixed
  safe string, never interpolated PII.
- **Sensitive-ID masking is total:** full Aadhaar/PAN exists only encrypted at rest and
  in-memory during processing; API responses, UI, and logs see masked only.
- **Field-level encryption at rest** (FR2): every `ProfileField` value + snippet is AES-256-GCM
  encrypted; GCM makes silent tampering detectable; AAD binds ciphertext to its `(profile,
  field)` so it can't be relocated.
- **Ownership scoping:** all document/profile reads filter by `user_id`; cross-user → `404`.
- **Upload abuse:** enforce `max_upload_bytes` + content-type allowlist; reject oversized before
  buffering the whole file; a PDF page-count cap (e.g. ≤ 10) to bound extraction cost.
- **Third-party processing:** document images are sent to Anthropic's vision API for extraction
  — note this as an explicit processing disclosure (surface at upload); no image is logged
  locally. (A self-hosted/offline OCR path is a future option, not built now.)
- **Confidence is grounded, not self-reported** (PRD §10): a value the model is "sure" of but
  which isn't in the returned snippet or fails its format check scores **low** and is flagged.
- **`type_mismatch` safety:** a wrong-schema extraction never silently populates the profile.
- **Job durability** (NFR): enqueue failures are surfaced (`503` + doc `failed`); transient
  extraction errors retry with backoff; jobs are idempotent (delete-then-insert), so a retry
  can't create duplicate candidates.
- **Startup validation:** a missing/short `PII_ENCRYPTION_KEY` or missing `ANTHROPIC_API_KEY`
  fails fast with a clear message rather than 500-ing mid-upload.
- **Deferred hardening (noted, not built):** per-user upload rate limiting, virus/malware scan
  of uploads, S3 server-side encryption (SSE-KMS) in addition to app-level field encryption,
  Celery task for the third-party-processing consent audit trail.

---

## 9. Metrics seam (full instrumentation is Phase 6)

Phase 1 makes ingestion latency **recoverable**, not yet dashboarded: `Document.created_at`
→ `Document.extracted_at` = upload→profile-ready latency; `confidence_band` distribution across
`ProfileField` rows gives an early read on how much lands auto-accepted vs. needs-confirmation.
No `metrics/` code this phase.

---

## 10. Testing (`backend/tests`, `frontend`)

**Backend (pytest):**
- **encryption:** encrypt→decrypt round-trip; wrong-AAD fails; tampered ciphertext fails (GCM);
  bad key length → clear startup error; mask helpers produce the documented masked forms.
- **validators:** PAN regex accept/reject; Aadhaar length + Verhoeff accept/reject; DOB parse +
  ISO normalize + implausible-year reject; gender mapping; `snippet_contains` normalization.
- **grounding rubric:** each row of §6.3 maps to the expected `confidence`/`band`/`status`
  (mock `vision_llm.extract`, **no real API call**).
- **`ocr_extract_task`:** mocked storage + vision → writes candidates, `extracted`/`partial`
  statuses; transient error triggers retry; terminal error → `failed` + safe `ocr_error`;
  `type_mismatch` writes **no** fields; re-run is idempotent (no duplicate candidates).
- **storage:** `moto`-mocked S3 put/get/delete round-trip + `ensure_bucket`.
- **API:** upload validates size/type + enqueues (mock Celery); `status` scoped to owner
  (cross-user → `404`); `GET /profile` **never** returns a full Aadhaar/PAN; `confirm`/`correct`
  transition status; `correct` rejects a value that fails the field validator (`422`).
- **multi-candidate:** two docs contributing `full_name` produce two rows, both retrievable,
  neither overwritten.

**Frontend (vitest, light):** Upload polls status and renders terminal states incl.
`type_mismatch`; profile view renders correct green/yellow/red bands and masks sensitive values;
confirm/correct call the client and update in place.

---

## 11. File-by-file change list

**Backend — implement (currently stubs):**
`core/encryption.py`, `services/storage.py`, `services/ocr/vision_llm.py`,
`services/extraction.py`, `workers/tasks.py` (`ocr_extract_task`),
`api/routes/documents.py`, `api/routes/profile.py`, `models/document.py`, `models/profile.py`
(`Profile` + `ProfileField`), `config.py` (§5 settings + key/API-key startup validation),
`main.py` (call `storage.ensure_bucket()` on startup).

**Backend — new:**
`schemas/document.py`, `schemas/profile.py`, `services/preprocessing.py` (HEIC/PDF/orient),
`core/validators.py` (PAN/Aadhaar-Verhoeff/DOB/gender + `snippet_contains`),
`db/migrations/versions/0002_profile_ingestion.py`.

**Backend — edit:** `db/base.py` (import `Document`, `Profile`, `ProfileField`),
`api/deps.py` (add an owner-scoped `get_owned_document` helper if useful),
`pyproject.toml` (add `pillow-heif`; dev `moto`).

**Frontend — new/implement:**
`pages/Upload.tsx` (real), `pages/Profile.tsx`, `components/ConfidenceField.tsx`,
`api/client.ts` (add methods), `types.ts` (add types), route wiring in `App.tsx`.

**Infra / config:** `.env.example` (§5 vars; `PII_ENCRYPTION_KEY` now required + generator
hint; `ANTHROPIC_API_KEY` required). No new compose services (MinIO already present from
Phase 0).

**Untouched this phase (stay stubs):** everything under `agent/`, `services/ocr/tesseract.py`,
`services/form_renderer.py`, `metrics/`, `models/form.py`, and the forms/history routers.
`DELETE /api/profile` stays a stub (Phase 5).

---

## 12. Non-goals reminder (don't drift)
No form upload, schema ID, field mapping, agent graph, verification of *mapped* values, form
rendering, Tesseract path, metrics dashboards, or profile deletion in Phase 1 — those are
Phases 2–6. Phase 1 exists solely to turn an uploaded ID document into a **trustworthy,
encrypted, source-attributed profile** that later phases fill forms from. Confidence here is
grounded in the **extracted value's own source snippet**; it is the seed of, not a substitute
for, the Phase 3 `document_verification_tool`.

---

## 13. Acceptance checklist (Done-When, enumerated)
1. Upload an Aadhaar image (drag-drop or camera) with `doc_type=aadhaar` → `202` + a
   `document_id`.
2. Poll `GET /documents/{id}/status` → transitions `pending`→`processing`→`extracted`
   (or `partial`).
3. `GET /profile` returns the extracted fields, each with a **grounded** confidence + band +
   `source.document_id`; Aadhaar number is **masked**.
4. High-stakes / low-confidence fields come back `needs_confirmation`; `confirm` and `correct`
   move them to `user_confirmed`/`user_corrected`.
5. The raw document is retrievable (owner-only) via `GET /documents/{id}/file` for side-by-side
   review, and still present in S3 (Phase-5 purge deletes it later).
6. Uploading a PAN image labeled `aadhaar` yields `ocr_status=type_mismatch` and **no** profile
   fields written.
7. At-rest inspection: `profile_fields.value_encrypted` is ciphertext (not readable plaintext);
   no log line or API response contains a full Aadhaar/PAN.
8. A transient extraction failure retries and can still succeed; an unreadable image ends
   `failed` with a safe reason.
9. `ruff`/`mypy` clean; new backend tests + light frontend tests green (per [dev-environment]).
