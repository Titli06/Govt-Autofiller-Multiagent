# SPEC — Phase 6: Metrics Instrumentation

Scope-locked spec for **Phase 6 only** of [PLAN.md](PLAN.md) — the final phase. Makes the PRD §9
success metrics a **real, queryable deliverable** rather than an assumption: per-form latency +
auto-fill/review breakdown, and per-user aggregates (auto-fill rate, schema-inference success rate,
mapping-tier distribution, verification pass rate, an accuracy proxy, and an estimated time-saved).
Most of it is arithmetic over columns Phases 1–5 already persist; this phase adds exactly **one new
column** (`FormField.mapping_tier`), **one new table** (`pipeline_run`), one aggregate endpoint, a
lightweight metrics page, and a standalone offline accuracy-eval harness.

> **Authority:** [PLAN.md](PLAN.md) Phase 6 + [govform-autofiller-prd.md](govform-autofiller-prd.md)
> §9 Success Metrics (% fields auto-filled at high confidence; accuracy vs. ground truth;
> time saved per form; end-to-end pipeline latency; schema-inference success rate), §8 NFR
> Performance ("full pipeline latency should be **reported and tracked as a core metric, not
> assumed**") and Auditability, and [CLAUDE.md](CLAUDE.md) ("Metrics ... are part of the
> deliverable"; never log/return raw PII; deletion is first-class — the purge must stay complete).
>
> Builds on every prior phase. The seams this phase wires up were deliberately left "recoverable,
> not dashboarded" in Phases 3–5 (SPEC-PHASE3.md §13, SPEC-PHASE4.md §13, SPEC-PHASE5.md §11) — this
> spec turns them into instrumentation. Where PLAN and PRD are silent, the decisions in §2 were made
> in the Phase 6 build interview and are **binding for this phase**.

---

## 1. Objectives & Done-When

**Done when:** every completed form reports its **latency** (upload → filled-form-ready) and its
**auto-fill vs. review breakdown**, and a **per-user aggregate metrics view** surfaces auto-fill
rate, high-confidence share, schema-inference success rate, mapping-tier distribution, verification
pass rate, an accuracy proxy, and an estimated time-saved — all queryable via `GET /api/metrics` and
visible on a lightweight Metrics page. A standalone offline harness computes true accuracy against a
labeled fixture set. The Phase-5 data purge stays complete: it also deletes the new metrics rows.

Acceptance is enumerated in §12.

### In scope
- **`FormField.mapping_tier`** — a new nullable `String(16)` column persisting the tier
  (`exact`/`strong`/`weak`/`none`) already computed by `confidence_scorer_tool` and currently
  **discarded** at persistence (the PLAN's "⚠️ blocked without a schema change"). Unblocks the
  mapping-tier-distribution metric. `NULL` for template fields (mirrors how `placement` was added).
- **`pipeline_run` table** — one row per fill (Decision 1, the "hybrid" storage model), holding the
  **coarse** latency spans + snapshot counters that make aggregate reads cheap and self-contained.
  Written at fill completion, updated at approval (Decision 8). Deleted by the Phase-5 purge.
- **`metrics/instrumentation.py`** — `record_fill()` (called from `fill_form_task`'s terminal
  branches) and `record_review()` (called from the review endpoint when a form reaches `approved`).
  Coarse spans from existing timestamps; **no per-stage sub-timers** (Decision 4).
- **`GET /api/metrics`** — a **per-user** (Decision 7) aggregate projection over `pipeline_run` +
  `form_fields` metadata. Counts/averages/ratios only — **no decryption, no field values, no PII.**
- **History latency** — `GET /api/history`'s `HistoryItemOut` gains `fill_latency_ms` /
  `review_latency_ms` so the per-form latency lands on the existing per-form view (see §6.6 for how
  this resolves the Q2-vs-Done-when tension without duplicating the projection).
- **Accuracy: live proxy + offline harness** (Decision 3). The **live** metric is the
  approved-as-is-vs-corrected proxy (a correction signals the auto-fill was wrong). The **offline**
  `scripts/eval_accuracy.py` computes true accuracy over a small committed labeled fixture set — run
  manually with credentials, **never in CI**.
- **Purge extension** — `DELETE /api/profile` also deletes the user's `pipeline_run` rows, following
  the same explicit-delete-by-`user_id` pattern (not a bare FK cascade), keeping FR10 whole.
- **Frontend** — a lightweight **Metrics** page (aggregate cards) + a latency column on History.

### Out of scope (defer / never)
- **Per-stage latency breakdown** (preprocess/classify/lookup/verify/Document-AI/render sub-timers) —
  Decision 4 chose coarse spans. A `pipeline_run` schema that could later carry them is noted, not
  built.
- **Global / cross-user aggregates** — Decision 7 is per-user only. No `GET /api/metrics/global`.
- **A separate deletion-audit table** — Decision 6: metrics are user data, the purge deletes them;
  the existing PII-free `profile_purge …` structured log line is the only deletion record. No
  self-exempt audit row.
- **Prometheus / OpenTelemetry / external metrics backends, real-time streaming, alerting** — the
  deliverable is the PRD §9 numbers, computed from the DB. No new infra.
- **A metrics dashboard for anyone but the authenticated owner**; **cost/token metrics**; **SLA/uptime
  monitoring** — not this project.
- **Auto-submit** — never, any phase (FR7).

---

## 2. Decisions carried from the interview (binding for Phase 6)

| # | Area | Decision |
|---|---|---|
| 1 | **Storage model** | **Hybrid.** Add `FormField.mapping_tier` (unblocks tier distribution) **and** a lightweight `pipeline_run` table (one row per fill) holding coarse latency spans + snapshot counters; derive everything else on-read from existing columns. Not a bare derive-on-read, not a full per-stage table. |
| 2 | **Metrics surface** | **Separate Metrics page + `GET /api/metrics` aggregate endpoint.** Per-form detail stays on History (extended with latency, §6.6); the new page shows aggregates only. Do **not** duplicate the per-form projection. |
| 3 | **Accuracy metric** | **Both.** Ship the **live** approved-as-is-vs-corrected proxy as the product metric, **and** a standalone **offline** ground-truth harness over a labeled fixture set. The proxy is the dashboard number; the harness validates that confidence correlates with correctness (PRD §9's stated point). |
| 4 | **Latency scope** | **Coarse spans only.** Time the form fill (`created_at → filled_at` = PRD's "upload → filled form ready") and review (`filled_at → approved`); also expose OCR-ingestion latency (`Document.created_at → extracted_at`) since it's already recoverable. **No** sub-stage timers. |
| 5 | **Time saved** | **Config seconds-per-field estimate.** `manual_seconds_per_field` (default 45) × `total_fields` = estimated manual time; `time_saved = estimate − measured review time`. Honestly labeled an **estimate**, config-tunable. |
| 6 | **Audit trail** | **No separate audit; the purge deletes all metrics.** `pipeline_run` rows are user data and are purged with everything else (FR10 stays absolute). The existing PII-free purge log line is the deletion record — **no** self-exempt `deletion_event` table. |
| 7 | **Aggregate scope** | **Per-user only.** `GET /api/metrics` aggregates strictly over the caller's own forms (`user_id`-scoped, like every other endpoint). No cross-user/global exposure. |
| 8 | **Row lifecycle** | **Write at fill completion, update at approval.** `record_fill()` inserts/overwrites the row (idempotent, mirroring `_persist_form_fields`' re-run) with fill latency + counters when the draft lands; `record_review()` updates review latency + accuracy-proxy counts when the form reaches `approved` (including re-approval after a Phase-3 reopen). |
| 9 | **Spec location** | This file — `SPEC-PHASE6.md`. Earlier specs unchanged; PLAN's Phase 6 heading links here. |

### Default implementation choices (not interviewed; set here)
- **`GET /api/metrics` returns a single aggregate object** (§5.2) — counts, averages (ms), and ratios
  (`0..1`, or `null` when the denominator is 0 so the UI can show "n/a" rather than a fake `0`).
- **`pipeline_run` carries a denormalized `user_id`** (FK → `users.id`, `ON DELETE CASCADE`, indexed)
  in addition to `form_id` (FK → `forms.id`, `ON DELETE CASCADE`) so both the per-user aggregate read
  **and** the explicit purge delete key off `user_id` without a join — matching the established
  explicit-delete-by-`user_id` purge pattern (SQLite in tests doesn't enforce FK actions).
- **A row is written for every terminal outcome** (`approved` | `in_review` | `failed` |
  `type_mismatch`), not just successful fills — so the schema-inference-success-rate **denominator**
  (inferred fills reaching a usable state vs. `failed`) is complete. `failed`/`type_mismatch` rows
  carry `total_fields=0`.
- **No decryption anywhere in the metrics path** — every counter reads non-encrypted metadata columns
  (`needs_review`, `confidence_band`, `verified`, `review_action`, `mapping_tier`, `schema_source`,
  timestamps, `value_encrypted IS NULL`). History-style: metadata + counts only.
- **DB access / IDs / timestamps / auth:** unchanged from Phases 0–5 — sync SQLAlchemy 2.0,
  `psycopg` v3, `get_current_user` + `get_db` deps, `TIMESTAMP WITH TIME ZONE` UTC, the `_aware()`
  naive→UTC normalizer pattern for SQLite-round-tripped datetimes.

---

## 3. Data model changes (migration `0006_metrics`)

### 3.1 `FormField.mapping_tier` (new column)
```python
# models/form.py — FormField, alongside `placement` (Phase 4)
# Semantic label→profile mapping tier for an INFERRED field: exact | strong | weak | none.
# NULL for template fields (their mapping is hand-authored, not tiered). Computed in
# confidence_scorer_tool.score() and, before Phase 6, discarded at persistence — this column
# is what makes the mapping-tier-distribution metric (PRD §9 / SPEC-PHASE4.md §13) buildable.
mapping_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
```
Persisted in `_persist_form_fields` from `f.get("mapping_tier")` (already `None` for template fields —
the scorer sets it so; no `schema_source` guard needed, unlike `placement`).

### 3.2 `pipeline_run` (new table — one row per fill)
```python
# models/metrics.py (new)
class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # form_id is unique — one row per form; a re-run of fill_form_task upserts, never duplicates.
    form_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("forms.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    # Denormalized for per-user aggregate reads AND the explicit purge delete (no join needed).
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schema_source: Mapped[str] = mapped_column(String(16), nullable=False)      # template | inferred
    terminal_status: Mapped[str] = mapped_column(String(32), nullable=False)    # approved|in_review|failed|type_mismatch

    # Coarse spans (Decision 4). fill = created_at → filled_at (PRD "upload → filled form ready").
    fill_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # review = filled_at → approved. 0 when a form auto-approves at fill (no human step);
    # NULL while still in_review; set/overwritten by record_review() at (re-)approval.
    review_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Snapshot counters (cheap aggregate reads; also derivable from form_fields, kept for self-containment).
    total_fields: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    autofilled_fields: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # NOT needs_review
    reviewed_fields: Mapped[int | None] = mapped_column(Integer, nullable=True)           # needs_review (set at approval)
    approved_as_is: Mapped[int | None] = mapped_column(Integer, nullable=True)            # review_action in (approved, approved_blank)
    corrected_fields: Mapped[int | None] = mapped_column(Integer, nullable=True)          # review_action == corrected

    created_at / updated_at  # server_default=func.now(), onupdate=func.now()
```
Register `PipelineRun` in `db/base.py`'s aggregator so Alembic autogen + the worker import graph
see it (the same import-graph regression that bit Phase 1 — a new model must be reachable from
`app.db.base`).

### 3.3 Migration `0006_metrics`
Adds `form_fields.mapping_tier` (nullable) + creates `pipeline_run` (with its two FKs and the
`form_id` unique / `user_id` index). No backfill — pre-existing forms simply have no `pipeline_run`
row and `mapping_tier NULL`; the aggregate read tolerates their absence (they predate metrics).

---

## 4. Config, deps & env additions

`config.py` — reuse all prior knobs. Add one (Decision 5):
```python
# Time-saved estimate (Phase 6). No in-app manual-fill baseline exists, so manual time is
# ESTIMATED: manual_seconds_per_field × total_fields. Surfaced as an explicit estimate, never
# as a measurement. Tune to taste; 45s/field is a conservative hand-entry-plus-lookup guess.
manual_seconds_per_field: int = 45
```
`.env.example` — add commented guidance:
```
# Time-saved metric (Phase 6): estimated manual seconds per field (× field count).
# MANUAL_SECONDS_PER_FIELD=45
```
**No new backend or frontend dependency.** No new env secret, S3/bucket, or LLM/Document-AI call —
the metrics path makes **zero** external calls (it's pure DB arithmetic). The offline harness (§6.7)
reuses the *existing* Gemini/Document-AI stack; it adds no dependency and is never imported by the app.

---

## 5. Schemas

### 5.1 `schemas/history.py` (edit — add latency to the per-form projection)
```python
class HistoryItemOut(BaseModel):
    # ... all existing fields ...
    fill_latency_ms: int | None      # from pipeline_run; None for pre-Phase-6 forms
    review_latency_ms: int | None
```

### 5.2 `schemas/metrics.py` (new — the aggregate projection)
```python
class MetricsOut(BaseModel):
    forms_total: int
    forms_by_status: dict[str, int]                 # {approved, in_review, failed, type_mismatch}

    # Latency (coarse, Decision 4). Averages over rows with a non-null span; None if none.
    avg_fill_latency_ms: float | None
    avg_review_latency_ms: float | None
    avg_ocr_latency_ms: float | None                # Document.created_at → extracted_at

    # Auto-fill (PRD §9). autofill_rate = auto-filled (not needs_review) / total fields.
    total_fields: int
    autofilled_fields: int
    autofill_rate: float | None
    high_confidence_rate: float | None              # confidence_band=="high" / total (form_fields)

    # Schema inference (PRD §9 / Phase 4). Over INFERRED runs only.
    inferred_forms_total: int
    schema_inference_success_rate: float | None     # (in_review|approved) / inferred_total
    mapping_tier_distribution: dict[str, int]        # {exact, strong, weak, none} over inferred fields

    # Trust (Phase 3 seam).
    verification_pass_rate: float | None            # verified / fields-with-a-value
    accuracy_proxy: float | None                    # approved_as_is / (approved_as_is + corrected)

    # Time saved (Decision 5) — the manual figure is an ESTIMATE, not a measurement.
    manual_seconds_per_field: int                   # the config constant, echoed for transparency
    estimated_manual_seconds: int                   # total_fields × constant
    measured_review_seconds: int                    # Σ review_latency
    estimated_time_saved_seconds: int               # estimate − measured (may be negative; shown as-is)

    # Reuse (UC5 denominator). One profile per user, so forms_per_profile == forms_total.
    forms_per_profile: int
```
All ratio fields are `null` when their denominator is 0 (no fake zeros). No PII, no key, no value —
counts, averages, and ratios only.

---

## 6. Backend

### 6.1 `metrics/instrumentation.py` (replace the stub)
Two functions; both take an open `Session` and **do not commit** (the caller's terminal `db.commit()`
in the task / endpoint persists them atomically with the status change).

```python
def record_fill(db: Session, form: Form, field_results: list[dict] | None) -> None:
    """Upsert the pipeline_run row when a fill reaches a terminal state (Decision 8).
    Idempotent: a re-run of fill_form_task overwrites the same row (form_id is unique),
    mirroring _persist_form_fields' delete-then-rewrite. field_results is None on a
    failed/type_mismatch fill (0 counts)."""
    run = db.scalar(select(PipelineRun).where(PipelineRun.form_id == form.id)) or PipelineRun(form_id=form.id)
    run.user_id = form.user_id
    run.schema_source = form.schema_source
    run.terminal_status = form.status
    run.fill_latency_ms = _span_ms(form.created_at, form.filled_at)
    fields = field_results or []
    run.total_fields = len(fields)
    run.autofilled_fields = sum(1 for f in fields if not f["needs_review"])
    if form.status == "approved":            # zero outstanding → auto-approved, no human review span
        run.review_latency_ms = 0
        run.reviewed_fields = 0
        run.approved_as_is = 0
        run.corrected_fields = 0
    else:                                    # in_review/failed/type_mismatch → review span not yet known
        run.review_latency_ms = None
    db.add(run)

def record_review(db: Session, form: Form) -> None:
    """Update the row when a form reaches `approved` via the review endpoint (incl. a
    re-approval after a Phase-3 reopen — overwrite, don't append). Defensive no-op if
    no row exists (a pre-Phase-6 form)."""
    run = db.scalar(select(PipelineRun).where(PipelineRun.form_id == form.id))
    if run is None:
        return
    rows = db.query(FormField).filter(FormField.form_id == form.id).all()
    run.review_latency_ms = _span_ms(form.filled_at, _now())
    run.reviewed_fields = sum(1 for r in rows if r.needs_review)
    run.approved_as_is = sum(1 for r in rows if r.review_action in ("approved", "approved_blank"))
    run.corrected_fields = sum(1 for r in rows if r.review_action == "corrected")
    run.terminal_status = form.status
```
`_span_ms(a, b)` → `int((_aware(b) - _aware(a)).total_seconds() * 1000)`, or `None` if either is
`None`. `_now()` / `_aware()` mirror the existing helpers.

### 6.2 Instrumentation call sites (`workers/tasks.py`)
`record_fill(db, form, …)` is called in each terminal branch of `_run_fill`, **before** that branch's
`db.commit()`, so the metrics row commits in the same transaction as the status flip:
- **success** (after `form.status`/`filled_at` set): `record_fill(db, form, result["fields"])`.
- **type_mismatch** branch: `record_fill(db, form, None)`.
- **`_fail_form`** (shared by every failure incl. zero-inferred-fields, retries-exhausted,
  classification/mapping/verification failure): `record_fill(db, form, None)` right before its
  `db.commit()`. (`_fail_form` already sets `form.filled_at`, so the fill span is defined.)

`ocr_extract_task` gets **no** new row — OCR latency is read on-demand from `Document.created_at →
extracted_at` (§6.5); no per-document metrics table.

### 6.3 Review call site (`api/routes/forms.py`)
In `submit_review_action`, after `form.status` is recomputed and committed-to:
```python
form.status = "in_review" if outstanding else "approved"
if form.status == "approved":
    record_review(db, form)          # sets review latency + approved-as-is/corrected counts
db.commit()
```
Placed inside the existing handler; no new endpoint, no behavior change to the review flow itself.

### 6.4 `GET /api/metrics` (`api/routes/metrics.py` — new router, mounted under `/api/metrics`)
Per-user (Decision 7). Two reads, both `user_id`-scoped, aggregated in Python (portable across the
SQLite/Postgres split, mirroring History):
1. **`pipeline_run` rows** for the user → `forms_total`, `forms_by_status`, latency averages, field
   totals/auto-filled, inferred success rate, accuracy-proxy counts, time-saved.
2. **One grouped read over `form_fields` joined to `forms`** (WHERE `forms.user_id == me`) pulling
   `mapping_tier`, `confidence_band`, `verified`, and `value_encrypted IS NULL` →
   `mapping_tier_distribution` (over inferred fields, i.e. `mapping_tier IS NOT NULL`),
   `high_confidence_rate` (band `high` / total), and `verification_pass_rate` (verified /
   fields-with-a-value). **No N+1; no decryption.**
3. **OCR latency:** a small read of `Document.created_at`/`extracted_at` for the user's extracted
   documents → `avg_ocr_latency_ms`.

Formulas are enumerated in **§11**. Empty account → all-zero counts, all ratios `null`, `200`.

### 6.5 OCR-ingestion latency
Recoverable, not persisted in a metrics row: `Document.extracted_at − Document.created_at` for
documents that reached a terminal extracted state. Averaged in the metrics read (documents with a
non-null `extracted_at`). No schema change — `extracted_at` and `created_at` already exist (Phase 1).

### 6.6 History latency (resolving the Q2 ↔ Done-when tension)
The Done-when requires **each form** to report its latency; Decision 2 keeps **aggregates** off
History (on the new Metrics page) to avoid duplicating the per-form projection. The clean resolution:
per-form latency belongs on the **existing per-form view** (History), aggregates on the **new**
page — no duplication. `GET /api/history` gains `fill_latency_ms`/`review_latency_ms` per row via a
**single grouped read** of `pipeline_run` for the returned form ids (same anti-N+1 pattern History
already uses for field counts), left-joined so a pre-Phase-6 form shows `null`. No new History
endpoint; the Metrics page never re-lists per-form rows.

### 6.7 Offline accuracy harness (`scripts/eval_accuracy.py` — new, standalone)
True ground-truth accuracy (Decision 3), **run manually**, never imported by the app, never in CI:
- **Fixtures:** a small committed set under `backend/tests/fixtures/eval/` — a few blank-form images
  + a `manifest.json` mapping each fixture to `{form_type, expected: {field_name: value}}` (synthetic
  data only, **no real PII**; consistent with the synthetic Aadhaar/PAN fixtures used live in Phases
  1–4).
- **Run:** for each fixture, invoke the same fill pipeline (`build_graph()` with real
  `classify_form`/`verifier`/`field_detector`/`label_mapper`, requiring GEMINI/Document-AI creds like
  the live-stack verification), compare each auto-filled value to `expected`, and report
  **precision** (auto-filled-correct / auto-filled), **recall** (auto-filled-correct / expected), and
  the **confidence-vs-correctness** correlation the proxy stands in for.
- **Output:** a plain-text/JSON summary to stdout — no DB writes, no persistence. Documented in
  README as a manual eval step and explicitly flagged as **not part of `pytest`** (it makes billed
  external calls, exactly like the live-stack checks).

### 6.8 Purge extension (`api/routes/profile.py` — `delete_my_data`)
Add one explicit delete inside the existing single transaction, **before** the `forms` delete (so it
never depends on FK-cascade ordering that SQLite won't enforce), keyed by `user_id` like the rest:
```python
db.execute(delete(PipelineRun).where(PipelineRun.user_id == user.id))   # Phase 6: metrics are user data (FR10)
```
The response contract (`DeleteProfileResponse`) is **unchanged** — `pipeline_run` rows aren't a
user-facing count. The existing PII-free `profile_purge …` log line remains the deletion record
(Decision 6); no new audit row. A test asserts a purge leaves **zero** `pipeline_run` rows for the
user and that a second user's rows are untouched.

---

## 7. Frontend (`frontend/src`)

- **Metrics page (`pages/Metrics.tsx`, new):** on mount, `GET /api/metrics`; render aggregate cards —
  auto-fill rate + high-confidence share, avg fill/review/OCR latency, schema-inference success rate,
  a small mapping-tier distribution bar (`exact/strong/weak/none`), verification pass rate, the
  accuracy proxy, and a **clearly-labeled *estimated*** time-saved (never presented as measured).
  Ratios that come back `null` render as "n/a", not `0%`. Add a route + a nav link alongside
  History/Upload/Profile behind `ProtectedRoute`.
- **History (`pages/History.tsx`):** add a **latency** column/detail per row from the new
  `fill_latency_ms` (and `review_latency_ms` where present), formatted human-friendly (e.g. "4.2 s").
  No aggregate summary strip here — aggregates live on the Metrics page (Decision 2).
- **`types/index.ts`:** add `MetricsOut`; extend `HistoryItem` with `fill_latency_ms` /
  `review_latency_ms` (`number | null`).
- **`api/client.ts`:** add `getMetrics(): Promise<MetricsOut>`. No other endpoint changes.

---

## 8. Security & edge cases (must-handle)
- **No PII, ever** (CLAUDE.md): the entire metrics path reads only non-encrypted metadata columns +
  timestamps + `value_encrypted IS NULL`; it **never decrypts**, never returns a value/key/label. The
  response is counts/averages/ratios. Same posture as History.
- **Strict per-user scoping** (Decision 7): every metrics read and the purge delete are
  `WHERE user_id == me`; a second user's runs/fields are provably invisible and untouched (two-user
  test). No global endpoint exists to leak across users.
- **Purge stays complete** (FR10, Decision 6): `pipeline_run` is deleted with everything else; no
  audit row survives to orphan. A test asserts zero metrics rows remain post-purge.
- **Idempotent instrumentation** (Decision 8): `record_fill` upserts by unique `form_id`, so a
  retried/re-run fill never double-counts; `record_review` overwrites on re-approval after a reopen,
  so review latency/accuracy counts reflect the **final** approved state, not a stale first pass.
- **Divide-by-zero / empty account:** every ratio guards its denominator and returns `null` (not `0`)
  when there's nothing to average/divide; an account with no forms → all-zero counts, all-null ratios,
  `200`.
- **Pre-Phase-6 data:** forms without a `pipeline_run` row and fields with `mapping_tier NULL` are
  tolerated — latency shows `null`, tier distribution simply doesn't count them. No backfill, no crash.
- **Negative time-saved:** if measured review time exceeds the estimate, `estimated_time_saved_seconds`
  is shown **as-is** (possibly negative) rather than clamped — honesty over flattery (Decision 5's
  "labeled an estimate").
- **Instrumentation must never break a fill or a review** — `record_fill`/`record_review` are plain
  arithmetic on already-loaded objects in the same session/transaction; they add no external call and
  no new failure mode. (If defensively wrapping is warranted, a failure there must not fail the fill —
  but there is nothing to fail.)
- **No auto-submit / masking boundaries touched:** metrics expose no field values, so no masking
  question arises; download gating, verification, and the HITL flow are unchanged.

---

## 9. Testing (`backend/tests`, `frontend`)

**Backend (pytest):**
- **`mapping_tier` persistence:** an inferred fill writes `exact/strong/weak/none` onto
  `FormField.mapping_tier`; a **template** fill leaves it `NULL` (regression: template scoring/persist
  is byte-for-byte unchanged).
- **`record_fill`:** on success writes a `pipeline_run` with correct `fill_latency_ms`, `total_fields`,
  `autofilled_fields`, `schema_source`, `terminal_status`; an auto-approved (zero-outstanding) fill
  sets `review_latency_ms=0` and zeroed review counts; a `failed`/`type_mismatch` fill writes a row
  with `total_fields=0`; a **re-run** upserts (one row, not two).
- **`record_review`:** flipping a form to `approved` sets `review_latency_ms` (> 0), `reviewed_fields`,
  `approved_as_is`, `corrected_fields` from `review_action`; a **reopen → re-approve** overwrites (not
  appends) and reflects the final state; a form with no row is a safe no-op.
- **`GET /api/metrics`:** aggregates correct over a seeded mix (template + inferred, approved +
  in_review + failed + type_mismatch) — `autofill_rate`, `high_confidence_rate`,
  `schema_inference_success_rate`, `mapping_tier_distribution`, `verification_pass_rate`,
  `accuracy_proxy`, latency averages, time-saved math; **empty account → all-zero/all-null, `200`**;
  denominators of 0 → `null` (not `0`); **cross-user isolation** (two-user fixture — A's metrics
  exclude B's forms); no decryption occurs (spot-check no PII in the response).
- **History latency:** `HistoryItemOut` carries `fill_latency_ms`/`review_latency_ms`; a pre-Phase-6
  form (no `pipeline_run`) → `null`; counts read via one grouped query (no N+1).
- **Purge extension:** `DELETE /api/profile` removes the user's `pipeline_run` rows (zero remain);
  a second user's rows survive; the response contract is unchanged; the PII-free purge log line still
  emits.
- **Migration `0006`:** applies (and, if reversible, downgrades) cleanly; `mapping_tier` + the
  `pipeline_run` table (with unique `form_id`, `user_id` index, both FKs) exist.

**Frontend (vitest, light):** Metrics page renders the aggregate cards from a mocked `getMetrics`,
shows "n/a" for `null` ratios, and labels time-saved as an estimate; History renders the new latency
column and tolerates `null`. `tsc`/ESLint/`vite build` clean after the `MetricsOut`/`HistoryItem`
additions.

**Not in CI:** `scripts/eval_accuracy.py` (billed external calls — run manually with creds, like the
live-stack verifications).

---

## 10. File-by-file change list

**Backend — new:**
`models/metrics.py` (`PipelineRun`; §3.2),
`schemas/metrics.py` (`MetricsOut`; §5.2),
`api/routes/metrics.py` (`GET /api/metrics`; §6.4),
`scripts/eval_accuracy.py` + `tests/fixtures/eval/manifest.json` (+ synthetic form fixtures; §6.7),
`db/migrations/versions/0006_metrics.py`.

**Backend — edit:**
`models/form.py` (`FormField.mapping_tier`; §3.1),
`db/base.py` (register `PipelineRun` in the aggregator; §3.2),
`metrics/instrumentation.py` (`record_fill`/`record_review`; §6.1 — replace the TODO stub),
`workers/tasks.py` (persist `mapping_tier` in `_persist_form_fields`; call `record_fill` in the three
terminal branches; §6.2),
`api/routes/forms.py` (call `record_review` on approval in `submit_review_action`; §6.3),
`api/routes/history.py` + `schemas/history.py` (add latency to `HistoryItemOut` via a grouped
`pipeline_run` read; §5.1/§6.6),
`api/routes/profile.py` (`delete_my_data`: explicit `pipeline_run` delete by `user_id`; §6.8),
`main.py` (mount the metrics router under `/api/metrics`),
`config.py` (`manual_seconds_per_field`; §4),
`.env.example` (commented `MANUAL_SECONDS_PER_FIELD`).

**Frontend — new:**
`pages/Metrics.tsx` (§7).

**Frontend — edit:**
`types/index.ts` (`MetricsOut`; `HistoryItem` latency fields),
`api/client.ts` (`getMetrics`),
`pages/History.tsx` (latency column),
the app router/nav (Metrics route + link).

**Docs — edit:**
`README.md` — a "Metrics" section: what each PRD §9 number means, that latency/auto-fill/schema-
inference are live, that accuracy has a live proxy **and** an offline harness, that time-saved is an
**estimate** (config knob), and how to run `scripts/eval_accuracy.py` (with creds, not in CI).

**Untouched this phase:** the agent graph + tools (except `mapping_tier` is now *persisted*, not
recomputed — the scorer already emits it), `document_verification`, the renderer, `form_schema`/
`field_mapping`, the OCR worker's logic, and the review/download **behavior** (only an instrumentation
call is added). No auth/security changes.

---

## 11. Metric definitions (the deliverable — exact formulas)

All per-user (`WHERE user_id == me`). `R` = the user's `pipeline_run` rows; `F` = the user's
`form_fields` (joined via `forms`); `D` = the user's documents. `null` when a denominator is 0.

| Metric | Formula | Source | PRD §9 |
|---|---|---|---|
| **End-to-end latency** | `avg(r.fill_latency_ms)` over `R` with a non-null span | `Form.created_at → filled_at` | ✅ pipeline latency |
| **Review latency** | `avg(r.review_latency_ms)` over non-null | `filled_at → approved` | (time-saved input) |
| **OCR latency** | `avg(d.extracted_at − d.created_at)` over extracted `D` | Phase-1 timestamps | (ingestion visibility) |
| **Auto-fill rate** | `Σ autofilled_fields / Σ total_fields` | `R` (`not needs_review`) | ✅ % auto-filled at high conf |
| **High-confidence share** | `count(band=="high") / count(F)` | `form_fields.confidence_band` | ✅ (corroborates auto-fill) |
| **Schema-inference success** | `count(inferred & status∈{in_review,approved}) / count(inferred)` | `R.schema_source/terminal_status` | ✅ schema-inference success rate |
| **Mapping-tier distribution** | `count(F) group by mapping_tier` (inferred only) | `form_fields.mapping_tier` (new) | ✅ (SPEC-PHASE4 §13) |
| **Verification pass rate** | `count(verified) / count(F with a value)` | `form_fields.verified` | (trust; Phase-3 seam) |
| **Accuracy proxy (live)** | `Σ approved_as_is / (Σ approved_as_is + Σ corrected)` | `R` review counts | ✅ accuracy (proxy) |
| **Accuracy (offline)** | `auto-filled-correct / auto-filled` vs. labeled ground truth | `scripts/eval_accuracy.py` | ✅ accuracy vs. ground truth |
| **Estimated manual time** | `Σ total_fields × manual_seconds_per_field` | config (Decision 5) | ✅ time saved (baseline) |
| **Measured review time** | `Σ review_latency_ms / 1000` | `R` | ✅ time saved (measured) |
| **Estimated time saved** | `estimated_manual − measured_review` (shown as-is, may be negative) | derived | ✅ time saved per form |
| **Forms per profile (reuse)** | `forms_total` (one profile/user) | `R` count | UC5 denominator |

---

## 12. Acceptance checklist (Done-When, enumerated)
1. Migration `0006` applies cleanly: `FormField.mapping_tier` (nullable) exists and the `pipeline_run`
   table exists with unique `form_id`, indexed `user_id`, and both `ON DELETE CASCADE` FKs.
2. An **inferred** fill persists the mapping tier (`exact/strong/weak/none`) onto `mapping_tier`; a
   **template** fill leaves it `NULL` and is otherwise byte-for-byte unchanged from Phase 4/5.
3. Every terminal fill writes exactly one `pipeline_run` row (idempotent on re-run) with correct
   `fill_latency_ms`, `total_fields`, `autofilled_fields`, `schema_source`, `terminal_status`;
   `failed`/`type_mismatch` rows carry `total_fields=0`.
4. Reaching `approved` (at fill for a zero-flag form, or via the review endpoint) sets
   `review_latency_ms` and the `approved_as_is`/`corrected_fields`/`reviewed_fields` counts; a reopen
   → re-approve **overwrites** to the final state.
5. `GET /api/metrics` returns correct per-user aggregates (auto-fill rate, high-confidence share,
   schema-inference success rate, mapping-tier distribution, verification pass rate, accuracy proxy,
   coarse latency averages incl. OCR, and the **estimate-labeled** time-saved), with `null` for
   zero-denominator ratios and all-zero/all-null for an empty account.
6. Metrics are strictly per-user: another user's forms never appear in the caller's aggregates
   (two-user test); no global/cross-user endpoint exists.
7. Each History row reports its per-form latency (`fill_latency_ms`/`review_latency_ms`), computed via
   one grouped read (no N+1); a pre-Phase-6 form shows `null` rather than erroring.
8. The Metrics page renders the aggregate cards (ratios as "n/a" when `null`, time-saved clearly an
   estimate); History shows the latency column.
9. `scripts/eval_accuracy.py` computes true precision/recall over the committed labeled fixture set
   against ground truth, runs **outside** CI (billed calls), and is documented in the README.
10. `DELETE /api/profile` also deletes the user's `pipeline_run` rows (zero remain post-purge), leaves
    a second user's rows intact, keeps its response contract unchanged, and still emits the PII-free
    purge log line — FR10 stays whole with **no** self-exempt audit row.
11. The metrics path performs **no decryption** and returns **no** PII/key/value/label — counts,
    averages, and ratios only.
12. `ruff`/`mypy` clean; `tsc`/ESLint/`vite build` clean after the `MetricsOut`/`HistoryItem`
    additions; new backend tests + light frontend tests green.
```
