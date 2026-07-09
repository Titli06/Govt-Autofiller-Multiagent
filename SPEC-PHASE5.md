# SPEC — Phase 5: History + Data Deletion

Scope-locked spec for **Phase 5 only** of [PLAN.md](PLAN.md). Ships the two remaining
data-lifecycle slices of the product: a **History dashboard** of past filled forms (reuse
reference) and a **first-class, irreversible data-deletion** flow that honors the
data-minimization commitment. Both are thin DB → backend → frontend slices over data models
that already exist and already carry the correct FK cascade rules — **no new tables, no new
columns, no Alembic migration** (a first for this project; every prior phase added one).

> **Authority:** [PLAN.md](PLAN.md) Phase 5 + [govform-autofiller-prd.md](govform-autofiller-prd.md)
> UC5 (re-use one profile across multiple forms in a session), UC6 (data deletion / profile
> purge — "cascade delete of profile + documents + form history"), FR10 (**Must** — user can
> delete their profile and all associated documents/history), FR11 (**Should** — maintain a
> history of past filled forms for reuse reference), §8 Privacy NFR ("explicit, easy-to-find data
> deletion; no data retention beyond operational need"), and [CLAUDE.md](CLAUDE.md) ("Data
> deletion (profile + documents + history cascade) is a first-class feature, not an afterthought";
> never log raw PII).
>
> Builds directly on Phases 1–4. The models, FKs, and S3 helpers this phase drives were all put
> in place earlier **specifically** to make this cascade correct: `profiles`/`documents`/`forms`
> already `ON DELETE CASCADE` from `users`; `profile_fields` cascades from `profiles`;
> `form_fields` cascades from `forms`; the provenance FKs `FormField.profile_field_id` /
> `FormField.source_doc_id` are deliberately `ON DELETE SET NULL` (Phase 2) so an already-generated
> draft survives a *partial* profile/document edit with its pointer nulled — which is exactly why a
> **full** purge must delete `forms` **explicitly by `user_id`**, not lean on cascading through
> `profiles`/`documents` (see §6.2).
>
> Where PLAN and PRD are silent, the decisions in §2 were made in the Phase 5 build interview and
> are **binding for this phase**.

---

## 1. Objectives & Done-When

**Done when:** a user can file multiple forms reusing one profile (UC5 — already true, verified
here, not rebuilt), view them all in a **History** dashboard — including past inferred-schema forms,
clearly labeled — with a one-click path back to finish a review or re-download an approved PDF, and
**permanently delete everything** (profile, all profile fields, all documents, every form and its
rendered PDF, and every associated S3 object) in **one password-confirmed action**, while their
account itself survives so they can immediately start over.

Acceptance is enumerated in §12.

### In scope
- **`GET /api/history`** — the current user's past forms (all **non-transient** statuses:
  `in_review` | `approved` | `failed` | `type_mismatch`; **excludes** still-running
  `pending`/`processing`), newest first, each carrying `schema_source` so inferred forms are
  labeled (Decision 5/§6.1). Read-only projection; per-field detail stays on the existing
  `GET /api/forms/{id}` / `.../review` endpoints the rows deep-link to (Decision 6).
- **`DELETE /api/profile`** — the irreversible **data-only purge** (Decision 1): password-confirmed
  (Decision 3), blocked while jobs are genuinely in flight (Decision 4), gather-keys → best-effort
  S3 delete → single DB transaction (Decision 7), all-or-nothing at the DB layer, idempotent, and
  scoped strictly to the calling user. The **`User` row, session, and refresh token survive** — this
  clears *data*, not the account (§6.2).
- **UC5 reuse — verify, don't build:** confirm `profile_lookup_tool` reads the *current* encrypted
  profile on **every** fill (no per-session caching that could go stale mid-sitting), so three forms
  filed back-to-back all draw from one profile. This is already the Phase-2 behavior; Phase 5 adds a
  regression test, not code (§6.4).
- **Frontend:** a **History** dashboard (past forms + `schema_source` badge + status + field counts,
  deep-linking to Review/download); an **explicit, easy-to-find "Delete all my data"** flow with a
  password-confirmation modal and an unmistakable irreversible-action warning; and the **`FormType`
  → `string` type widening** the PLAN flagged (§7).

### Out of scope (defer)
- **Full account deletion** (deleting the `User` row, revoking tokens, logging out) — **not** this
  phase (Decision 1). Phase 5 is a *data* purge; the account stays usable. A separate
  `DELETE /api/account` is a clean future addition but is explicitly not built here.
- **Per-item deletion** (delete one document / one past form) — **not** this phase (Decision 2).
  Only the all-or-nothing purge ships, keeping the destructive surface and the atomicity story small.
- **Pagination / search / filtering** of history — v1 returns the full (personal-scoped, bounded)
  list newest-first. A `limit`/`offset` (or cursor) is a trivial later add; noted, not built (§6.1).
- **Metrics dashboards** — Phase 6. Cumulative time-saved (UC5) and the deletion audit trail are
  *recoverable* here, not visualized.
- **Soft delete / undo / export-before-delete / retention grace period** — deletion is **immediate
  and permanent** by design (FR10 / §8 Privacy). No trash can, no export bundle in v1.
- **Auto-submit** — never, any phase (FR7).

---

## 2. Decisions carried from the interview (binding for Phase 5)

| # | Area | Decision |
|---|---|---|
| 1 | **Deletion scope** | **Data-only purge; the account survives.** `DELETE /api/profile` destroys the profile, all profile fields, all documents, all forms + form fields, and every associated S3 object — but **keeps** the `User` row, the active session, and the refresh token. The user stays logged in and lands on an empty dashboard, free to re-upload and rebuild immediately. (Full account teardown is deliberately deferred — §1 Out of scope.) |
| 2 | **Granularity** | **All-or-nothing purge only.** No per-document or per-form delete endpoint this phase. Matches PLAN's "delete everything in one action" Done-when and keeps the atomicity/orphan story simple. |
| 3 | **Confirmation strength** | **Password re-entry.** The `DELETE` request body carries the user's **current password**, verified server-side with the same bcrypt path as login before anything is deleted. Proves it is really the user (not a hijacked session or a stray click) for an irreversible action; a mismatch → `403`, nothing deleted. |
| 4 | **In-flight jobs** | **Block while jobs are genuinely in flight** → `409`. If any of the user's `documents.ocr_status` or `forms.status` is `pending`/`processing` **and** was updated recently (within the staleness window, Decision 8), refuse the purge so a worker can't re-insert rows or write S3 objects *after* the delete. |
| 5 | **History scope** | **All non-transient forms** — `in_review`, `approved`, `failed`, `type_mismatch` — newest first. `pending`/`processing` (still working) are excluded. `failed`/`type_mismatch` **are** shown, with their safe reason, so a form that didn't complete is visible (and clearable via the purge), not silently gone. |
| 6 | **History actions** | **Deep-link to existing pages.** Each row links to the already-built Review page (`approved` → download via `GET /api/forms/{id}/download`; `in_review` → continue/finish review; `failed`/`type_mismatch` → show reason, no action). **No new backend endpoints** beyond `GET /api/history`; reuse Phase-3 review/download. |
| 7 | **Purge atomicity / ordering** | **Gather keys → best-effort S3 delete → single DB transaction → commit.** Read every `s3_key`/`rendered_s3_key`/document key first; delete each from S3 best-effort (already-absent = success; any error logged by count, never fatal, never PII); **then** delete the DB rows in one transaction and commit. **DB is the source of truth and always ends clean**; a rare S3 leak is logged for a later sweep and never blocks the user's right to delete. A single flaky S3 call must **not** wedge the purge. |
| 8 | **Stuck-job escape** | **Staleness cutoff on the in-flight block.** Only a `pending`/`processing` row whose `updated_at` is within `purge_stale_job_seconds` (default **900s**) blocks. A row stuck longer is treated as a dead job and does **not** block — so a crashed worker can never permanently wedge a privacy feature. |
| 9 | **No migration** | Phase 5 adds **no** DB schema change. Every FK cascade rule it relies on already exists (Phases 1–4). The "explicit `Form` delete by `user_id`" the PLAN calls for is **application logic in the purge handler**, not DDL. |
| 10 | **Spec location** | This file — `SPEC-PHASE5.md`. Earlier specs unchanged; PLAN's Phase 5 heading links here. |

### Default implementation choices (not interviewed; set here)
- **`DELETE /api/profile` returns `200` with a counts-only summary** (`{documents_deleted,
  forms_deleted, profile_fields_deleted, s3_objects_deleted, s3_delete_failures}`) rather than a
  bare `204`, so the UI can show "removed N documents, M forms." **Counts only — never any PII, key,
  or label** in the response (CLAUDE.md).
- **Idempotent:** a purge with nothing to delete returns `200` with all-zero counts, not a `404`.
- **DB delete order inside the transaction:** `forms` (by `user_id`, cascades `form_fields`) →
  `documents` (by `user_id`) → `profiles` (by `user_id`, cascades **all** `profile_fields`,
  including `origin="manual"` candidates that have no `source_doc_id`). Any order is FK-safe (the
  provenance FKs are `SET NULL`), but forms-first avoids needless `SET NULL` churn on rows about to
  be deleted anyway.
- **S3 helper:** reuse `services/storage.delete_document(key)` — it is a key-agnostic
  `delete_object` wrapper and works for `documents/…`, form-upload, and rendered-PDF keys alike
  (optionally aliased `delete_object` for clarity). Each call is wrapped best-effort (`try/except`,
  count failures). S3 `delete_object` is idempotent (deleting an absent key succeeds), so a
  double-purge or an already-swept key is a non-error.
- **History `display_name`** reuses the existing `forms.py` resolution (template `display_name` via
  `load_template`, degrading to the verbatim `declared_form_type` for inferred/free-text types) so an
  inferred "Marriage Certificate" shows its real label, not a registry key.
- **DB access / IDs / timestamps / auth:** unchanged from Phases 0–4 — sync SQLAlchemy 2.0,
  `psycopg` v3, `get_current_user` + `get_db` deps, `TIMESTAMP WITH TIME ZONE` UTC.

---

## 3. Data model changes

**None.** No new table, no new column, no migration (Decision 9). Every cascade this phase exercises
already exists:

| Relationship | Rule | Where it fires in the purge |
|---|---|---|
| `profiles.user_id → users.id` | `CASCADE` | (unused by data-only purge — we delete `profiles` explicitly by `user_id`) |
| `profile_fields.profile_id → profiles.id` | `CASCADE` | deleting `profiles` removes **all** profile fields (incl. `origin="manual"`, `source_doc_id=NULL`) |
| `documents.user_id → users.id` | `CASCADE` | (unused directly — we delete `documents` explicitly by `user_id`) |
| `profile_fields.source_doc_id → documents.id` | `CASCADE` | redundant with the `profiles` delete; harmless |
| `forms.user_id → users.id` | `CASCADE` | (unused directly — we delete `forms` explicitly by `user_id`) |
| `form_fields.form_id → forms.id` | `CASCADE` | deleting `forms` removes all form fields |
| `form_fields.profile_field_id → profile_fields.id` | `SET NULL` | forms are deleted first, so this never actually needs to null anything |
| `form_fields.source_doc_id → documents.id` | `SET NULL` | same — forms gone before documents |

> **Why explicit `forms` deletion is mandatory** (carried from PLAN's Phase-4 audit note): a full
> purge cannot rely on cascading *through* `profiles`/`documents`, because `Form.profile_field_id`
> and `FormField.source_doc_id` are `SET NULL`, **not** `CASCADE`. Deleting only the profile and
> documents would leave every completed `Form` row — and its `rendered_s3_key` PDF in S3 — alive with
> a nulled pointer. The purge handler therefore issues a direct `DELETE FROM forms WHERE user_id=…`.

---

## 4. Config, deps & env additions

`config.py` — reuse all Phase-0–4 knobs. Add one:
```python
# Data purge (Phase 5). A pending/processing document/form older than this is treated as a
# dead job and no longer blocks DELETE /api/profile, so a crashed worker can't wedge deletion.
# Must comfortably exceed a worst-case OCR/fill incl. retries (ocr/fill_max_retries=3 ×
# ~backoff + LLM latency). 900s (15 min) is generous headroom. (Decision 8.)
purge_stale_job_seconds: int = 900
```
`.env.example` — add commented guidance:
```
# Data purge (Phase 5): pending/processing jobs older than this (seconds) no longer block deletion.
# PURGE_STALE_JOB_SECONDS=900
```
**No new dependency** (backend or frontend). No new env secret. No S3/bucket change.

---

## 5. Schemas (`schemas/history.py` new; `schemas/profile.py` edit)

### 5.1 `schemas/history.py` (new)
```python
class HistoryItemOut(BaseModel):
    id: UUID
    form_type: str            # declared_form_type — free-text for inferred (Phase 4 Decision 4)
    display_name: str         # resolved template label, else the verbatim free-text type
    schema_source: str        # "template" | "inferred"  (badge on the row)
    status: str               # in_review | approved | failed | type_mismatch
    fill_error: str | None    # safe non-PII reason, set on failed/type_mismatch
    total_fields: int
    outstanding_fields: int   # needs_review AND NOT reviewed
    download_ready: bool      # status == "approved"
    created_at: datetime
    filled_at: datetime | None

class HistoryOut(BaseModel):
    forms: list[HistoryItemOut]
```

### 5.2 `schemas/profile.py` (edit)
```python
class DeleteProfileRequest(BaseModel):
    password: str             # current password; re-auth confirmation (Decision 3)

class DeleteProfileResponse(BaseModel):
    documents_deleted: int
    forms_deleted: int
    profile_fields_deleted: int
    s3_objects_deleted: int
    s3_delete_failures: int   # best-effort S3 leaks, surfaced for transparency (counts only)
```

---

## 6. Backend

### 6.1 `GET /api/history` (`api/routes/history.py` — replace the stub)
```python
@router.get("", response_model=HistoryOut)
def list_history(db=Depends(get_db), user=Depends(get_current_user)) -> HistoryOut:
    _VISIBLE = ("in_review", "approved", "failed", "type_mismatch")   # Decision 5
    forms = db.scalars(
        select(Form)
        .where(Form.user_id == user.id, Form.status.in_(_VISIBLE))
        .order_by(Form.created_at.desc())
    ).all()
    return HistoryOut(forms=[_to_history_item(db, f) for f in forms])
```
- `_to_history_item` computes `total_fields` and `outstanding_fields` (`needs_review AND NOT
  reviewed`) with a **single grouped count query** over `form_fields` for the returned form ids (or a
  correlated count) — not an N+1 per form. `download_ready = status == "approved"`. `display_name`
  reuses the `forms.py` template-resolution helper (degrades to `declared_form_type`).
- **No decryption, no field values** — history is metadata + counts only; values never leave the
  form/review endpoints. Nothing PII-bearing is read here.
- **Ownership:** every row filtered by `user_id`; a cross-user form is invisible (not a 404 — it
  simply isn't in the list).
- Empty result → `HistoryOut(forms=[])`, `200`.

### 6.2 `DELETE /api/profile` (`api/routes/profile.py` — new handler)
The irreversible data-only purge. Sequence (Decisions 1/3/4/7/8):
```python
@router.delete("", response_model=DeleteProfileResponse)
def delete_my_data(body: DeleteProfileRequest, db=Depends(get_db),
                   user=Depends(get_current_user)) -> DeleteProfileResponse:
    # 1. Re-auth (Decision 3) — reuse the exact login verification path.
    if not verify_password(body.password, user.hashed_password):
        raise _err(403, "Password is incorrect", "INVALID_PASSWORD")

    # 2. In-flight guard with staleness cutoff (Decisions 4/8).
    cutoff = utcnow() - timedelta(seconds=settings.purge_stale_job_seconds)
    busy = db.scalar(select(func.count()).select_from(Document).where(
                Document.user_id == user.id,
                Document.ocr_status.in_(("pending", "processing")),
                Document.updated_at >= cutoff)) \
         or db.scalar(select(func.count()).select_from(Form).where(
                Form.user_id == user.id,
                Form.status.in_(("pending", "processing")),
                Form.updated_at >= cutoff))
    if busy:
        raise _err(409, "A document or form is still being processed; try again shortly",
                   "JOBS_IN_PROGRESS")

    # 3. Gather every S3 key BEFORE any delete (Decision 7).
    doc_keys  = db.scalars(select(Document.s3_key).where(Document.user_id == user.id)).all()
    form_keys = [k for row in db.execute(
                    select(Form.s3_key, Form.rendered_s3_key)
                    .where(Form.user_id == user.id)).all()
                 for k in row if k]                    # rendered_s3_key is nullable
    all_keys = [*doc_keys, *form_keys]

    # 4. Best-effort S3 delete — never fatal, never PII (Decision 7).
    deleted, failed = 0, 0
    for key in all_keys:
        try:
            storage.delete_document(key)               # idempotent; absent key = success
            deleted += 1
        except Exception:
            failed += 1
            log.warning("purge: S3 delete failed", extra={"user_id": str(user.id)})  # no key/PII

    # 5. Single DB transaction — forms → documents → profile (all by user_id).
    n_forms  = db.execute(delete(Form).where(Form.user_id == user.id)).rowcount
    pf_ids   = select(ProfileField.id).join(Profile).where(Profile.user_id == user.id)
    n_pf     = db.scalar(select(func.count()).select_from(pf_ids.subquery())) or 0
    n_docs   = db.execute(delete(Document).where(Document.user_id == user.id)).rowcount
    db.execute(delete(Profile).where(Profile.user_id == user.id))   # cascades profile_fields
    db.commit()

    return DeleteProfileResponse(documents_deleted=n_docs, forms_deleted=n_forms,
        profile_fields_deleted=n_pf, s3_objects_deleted=deleted, s3_delete_failures=failed)
```
Notes:
- **`profile_fields_deleted`** is counted *before* the `profiles` delete cascades them (a count of
  the to-be-cascaded rows), so the summary is accurate.
- **`form_fields`** are not counted separately — they cascade from `forms` and aren't a user-facing
  number.
- **Account untouched (Decision 1):** no `User`, `RefreshToken`, or `EmailVerificationToken` row is
  deleted; the session/cookie is left intact. The user is still authenticated after the call.
- **Cross-user safety:** every `DELETE`/`SELECT` is `WHERE user_id == user.id`; another user's data
  is untouched — covered by an explicit two-user test (§9).
- **Rebuild path:** after the purge the `Profile` row is gone; the next document upload re-creates it
  via the Phase-1 ingestion path (which already lazily creates a `Profile` when absent). Verify this
  during build — the purge must leave the account in a clean, re-usable state, not a half-state.
- **Reuse `_err`** (already in `profile.py`); reuse `verify_password` + the user password-hash
  attribute from the Phase-0 login handler (do not re-implement bcrypt).

### 6.3 UC5 reuse — verify, don't build
`profile_lookup_tool` fetches the profile fresh from the DB on every `fill_form_task` invocation;
there is no cross-fill in-memory cache. Filing three forms in one sitting therefore reads one profile
three times, always current (a correction made between fill #1 and #2 is visible to #2). Phase 5 adds
a **regression test** asserting two sequential fills both read the same profile and pick up an
interleaved profile edit — **no production code**. (Cumulative time-saved quantification is Phase 6.)

---

## 7. Frontend (`frontend/src`)

- **History page (`pages/History.tsx`):** on mount, `GET /api/history`; render newest-first rows,
  each showing `display_name`, a **`schema_source` badge** ("Auto-detected" for `inferred`, subtle/no
  badge for `template`), a status pill, `created_at`, and `outstanding_fields`/`total_fields`. Row
  actions (Decision 6): `approved` → **Download** (existing `downloadForm`); `in_review` → **Continue
  review** link to the Review route; `failed`/`type_mismatch` → show `fill_error`, no action. Empty
  state → a friendly "No forms yet" with a link to Upload. (The PLAN's "profile data" half of the
  History view is already served by the existing Profile page / `GET /api/profile`; the dashboard may
  link to it rather than duplicate it.)
- **Delete-my-data flow:** an **explicit, easy-to-find** "Delete all my data" section (on History or
  a Settings/Profile area) with an unmistakable irreversible-action warning. Clicking opens a
  **confirmation modal** that (a) states exactly what will be destroyed (profile, documents, all
  forms + downloads) and that it **cannot be undone**, and (b) requires the user to **type their
  current password**; submit → `DELETE /api/profile` with `{ password }`. On `200`: clear client
  profile/history state, show a brief "Your data was deleted" confirmation with the returned counts,
  and route to the (now empty) dashboard — **the user stays logged in** (Decision 1). On `403` → "Password is
  incorrect," keep the modal open. On `409` → "A document or form is still processing; try again in a
  moment."
- **`types/index.ts` — the PLAN-flagged widening:** change `FormOut.form_type` and
  `FormReviewOut.form_type` from the narrow `FormType` union to **`string`** (Phase 4 made
  `declared_form_type` free-text for inferred forms; a History list *will* render real inferred-form
  strings). Fix the one `FORM_TYPE_LABELS[...]` indexing site (in `FormFill.tsx`'s `type_mismatch`
  message) to guard/fallback for an unknown key instead of assuming a union member. Add
  `HistoryItem`/`HistoryOut` and `DeleteProfileResponse` interfaces.
- **`api/client.ts`:** add `getHistory(): Promise<HistoryOut>` and
  `deleteMyData(password: string): Promise<DeleteProfileResponse>`. No other endpoint changes.

---

## 8. Security & edge cases (must-handle)
- **Re-auth before destruction** (Decision 3): wrong/missing password → `403 INVALID_PASSWORD`,
  **zero** deletion side effects (the check precedes every read/delete). A valid session alone is not
  sufficient to purge.
- **No silent leak, no wedge** (Decision 7): DB is transactional and always ends consistent; S3 is
  best-effort with a surfaced `s3_delete_failures` count. A flaky S3 endpoint cannot block deletion,
  and a partial S3 failure never leaves dangling DB rows.
- **In-flight race closed, with an escape** (Decisions 4/8): a *recent* running job blocks (so a
  worker can't resurrect purged data by committing after the delete); a *stale* one does not (so a
  dead worker can't permanently deny deletion). The window is config-tunable.
- **Idempotent & re-usable:** double-purge, or purging an account with no data, → `200` all-zeros;
  the account is immediately usable to rebuild (Profile lazily re-created on next upload).
- **Cross-user isolation:** history lists and the purge are strictly `user_id`-scoped; a second
  user's profile/documents/forms/S3 objects are provably untouched (two-user test, §9).
- **Manual-origin profile fields** (`origin="manual"`, `source_doc_id=NULL`) cascade correctly via
  `profile_id → profiles.id CASCADE` — no special-casing; the `profiles` delete gets them.
- **No PII in logs or responses** (CLAUDE.md): the purge logs by `user_id`/counts only — never a
  key, label, filename, or value; the response is counts only. History reads no encrypted values at
  all.
- **No auto-submit / masking / at-rest encryption boundaries:** unchanged; history exposes no field
  values, so no masking question even arises.
- **Deferred hardening (noted, not built):** full account deletion + token revocation, per-item
  delete, an orphaned-S3-key reconciliation sweep job, export-before-delete, a retention grace
  period / soft delete, and history pagination.

---

## 9. Testing (`backend/tests`, `frontend`)

**Backend (pytest):**
- **`GET /api/history`:** returns `in_review`/`approved`/`failed`/`type_mismatch` newest-first;
  **excludes** `pending`/`processing`; empty account → `[]`; an **inferred** form surfaces
  `schema_source="inferred"` and its verbatim free-text `display_name`; `total_fields`/
  `outstanding_fields`/`download_ready` correct; cross-user forms invisible; no N+1 (counts via one
  grouped query).
- **`DELETE /api/profile` — happy purge:** correct password → `200` with accurate counts; all
  `profiles`/`profile_fields`/`documents`/`forms`/`form_fields` rows for the user are gone;
  `storage.delete_document` called once per gathered key (docs + `s3_key` + non-null
  `rendered_s3_key`); the account/session/user row still exists afterward (Decision 1).
- **Re-auth:** wrong password → `403 INVALID_PASSWORD` and **nothing deleted** (rows + S3 untouched).
- **In-flight block:** a **recent** `processing` document or form → `409 JOBS_IN_PROGRESS`, nothing
  deleted; a **stale** one (`updated_at` older than `purge_stale_job_seconds`) → purge **proceeds**.
- **S3 best-effort:** a `delete_document` that raises for one key → the purge **still commits** the
  DB delete and reports `s3_delete_failures >= 1` (DB clean regardless); an absent key counts as a
  success (idempotent).
- **Idempotency / empty:** purging an account with no profile/documents/forms → `200`, all-zero
  counts, no error.
- **Cross-user isolation:** user A's purge leaves user B's profile, documents, forms, and S3 keys
  fully intact (two-user fixture).
- **Manual-origin cascade:** a `ProfileField(origin="manual", source_doc_id=None)` is deleted by the
  purge (via the `profiles` cascade).
- **UC5 reuse (regression):** two sequential `fill_form_task` runs read the same profile; a profile
  correction interleaved between them is visible to the second fill (no stale cross-fill cache).

**Frontend (vitest, light):** History renders rows with the `schema_source` badge and the correct
per-status action (download vs continue-review vs reason-only); the empty state shows; the delete
modal requires a typed password before enabling submit, calls `deleteMyData`, and on success clears
state and stays on an authed (empty) view; a `403` keeps the modal open with an error. `tsc`/ESLint
clean after the `FormType → string` widening and the guarded `FORM_TYPE_LABELS` call site.

---

## 10. File-by-file change list

**Backend — new:**
`schemas/history.py` (`HistoryItemOut`, `HistoryOut`; §5.1).

**Backend — edit:**
`api/routes/history.py` (implement `GET /api/history`; §6.1),
`api/routes/profile.py` (add `DELETE /api/profile` purge handler; §6.2 — reuse `verify_password`,
`_err`, `storage.delete_document`),
`schemas/profile.py` (`DeleteProfileRequest`, `DeleteProfileResponse`; §5.2),
`config.py` (`purge_stale_job_seconds`; §4),
`.env.example` (commented `PURGE_STALE_JOB_SECONDS` guidance),
`services/storage.py` (optional: add a `delete_object` alias for key-agnostic clarity — no behavior
change).

**Frontend — edit:**
`pages/History.tsx` (the dashboard + delete-my-data flow; §7),
`types/index.ts` (`FormType → string` widening on `FormOut`/`FormReviewOut.form_type`;
`HistoryItem`/`HistoryOut`/`DeleteProfileResponse`),
`api/client.ts` (`getHistory`, `deleteMyData`),
`pages/FormFill.tsx` (guard the one `FORM_TYPE_LABELS[...]` call site).

**Docs — edit:**
`README.md` — document the data-deletion guarantee (what a purge destroys, that the account
survives, the best-effort-S3 / transactional-DB posture) and the History view.

**Untouched this phase:** the agent graph + tools, `document_verification`, the fill/OCR workers, the
renderer, `form_schema`/`field_mapping`, the review/approve/download endpoints, `metrics/` (Phase 6),
and every model (no migration — Decision 9).

---

## 11. Metrics seam (full instrumentation is Phase 6)
Phase 5 makes these **recoverable**, not dashboarded: **profile-reuse count** (forms per user drawing
on one profile — the UC5 time-savings denominator) and a **deletion event** (the purge could emit a
counts-only structured log line for an audit trail). No `metrics/` code this phase.

---

## 12. Acceptance checklist (Done-When, enumerated)
1. `GET /api/history` returns the user's `in_review`/`approved`/`failed`/`type_mismatch` forms
   newest-first, **excludes** `pending`/`processing`, and carries `schema_source` (inferred forms
   labeled) + `total_fields`/`outstanding_fields`/`download_ready`; empty account → `[]`; cross-user
   forms are invisible.
2. Each history row deep-links correctly: `approved` → downloads its PDF via the existing endpoint;
   `in_review` → opens the Review page to finish; `failed`/`type_mismatch` → shows its safe reason.
3. `DELETE /api/profile` with the **correct password** destroys the profile, **all** profile fields
   (incl. `origin="manual"`), all documents, all forms + form fields, and **every** associated S3
   object (`Document.s3_key`, `Form.s3_key`, non-null `Form.rendered_s3_key`), returning accurate
   counts.
4. The **account survives** the purge: the `User` row, refresh token, and session remain; the user is
   still logged in and can immediately re-upload to rebuild a fresh profile.
5. A **wrong/missing password** → `403`, with **zero** deletion side effects.
6. A **recent** in-flight OCR/fill job blocks the purge (`409`); a **stale** one
   (> `purge_stale_job_seconds`) does not — deletion cannot be permanently wedged by a dead worker.
7. Purge atomicity holds: S3 keys are gathered first, S3 deletes are best-effort (a failure is
   counted in `s3_delete_failures`, never fatal), and the DB delete commits in one transaction so the
   DB always ends consistent; an absent S3 key is a non-error (idempotent).
8. Purging an account with no data → `200`, all-zero counts (idempotent, no `404`).
9. Cross-user isolation: one user's purge provably leaves every other user's profile/documents/
   forms/S3 objects intact.
10. UC5: filing multiple forms in a sitting reuses one profile (verified — each fill reads the
    current profile; an interleaved profile edit is picked up by the next fill).
11. The frontend History dashboard renders past forms (with the inferred/`schema_source` label and
    per-status actions) and offers an **explicit, easy-to-find, password-confirmed** delete-my-data
    flow with a clear irreversible-action warning.
12. **No migration** — `ruff`/`mypy` clean; the `FormType → string` widening leaves `tsc`/ESLint
    clean; new backend tests + light frontend tests green; **no raw PII, key, or label** in any purge
    log line or response (counts only).
```
