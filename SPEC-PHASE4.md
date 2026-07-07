# SPEC — Phase 4: Schema Inference for Unseen Forms

Scope-locked spec for **Phase 4 only** of [PLAN.md](PLAN.md). Ships the project's core
differentiating slice — DB → LangGraph agent → async worker → API → frontend — that finally
makes the **template-less path reachable**: a form the system has never seen is parsed for its own
fields (Google Document AI), each detected field **label** is *semantically* matched to the
canonical profile vocabulary (LLM, not string matching), the value is filled at a **discounted,
always-reviewed** confidence, and the result flows through the **exact same**
verification → HITL review → gated download pipeline Phase 3 built.

> **Authority:** [PLAN.md](PLAN.md) Phase 4 + [govform-autofiller-prd.md](govform-autofiller-prd.md)
> UC3 (fill an unseen form type — "more fields flagged for review, expected"), FR4 (infer a field
> schema for forms not in the known-template library), FR5 (map fields to profile data accounting
> for phrasing/format variance), §5.2 (LLM-based extraction chosen precisely because regex/string
> matching is "brittle across format variance" — **binding on the mapping mechanism**), §6 data-flow
> step 2 ("known template or inferred"), §9 (schema-inference success rate is a deliverable metric),
> §10 (schema-drift + hallucination risks; the "LLMs aren't built for pixel coordinates" risk that
> mandates Document AI over vision-LLM for boxes), and [CLAUDE.md](CLAUDE.md) (inferred schemas
> default to **lower** confidence; confidence grounded in source-document match, never LLM
> self-report; mandatory HITL; never auto-submit; every filled field auditable).
>
> Builds directly on Phase 3 ([SPEC-PHASE3.md](SPEC-PHASE3.md)) — the verification tool, the
> `confidence_scorer` policy, the review/approve/download endpoints, the PyMuPDF renderer, the
> `Form`/`FormField` review columns, and the Document-AI **interface + config** stubbed there
> (`services/form_placement/document_ai.py`, `documentai_*` settings). Phase 4 **finishes** the two
> things Phase 3 left "interface-only and provably unreachable": the Document AI call and the upload
> gate that blocks unknown form types.
>
> Where PLAN and PRD are silent, the decisions in §2 were made in the Phase 4 build interview and
> are **binding for this phase**.

---

## 1. Objectives & Done-When

**Done when:** a user uploads a form with **no template** → the system detects its fields via
Document AI, semantically maps what it can to profile data at a **discounted, always-reviewed**
confidence, runs it through the **same** verification/review/download pipeline as a known-template
form, fills what it can, places each fill at its detected box (or the appended "Additional fields"
page), and routes **everything** to review. A form the vision-LLM **confidently** recognizes as a
known type is filled from the **template** instead (better placement, hand-authored high-stakes) —
inference runs only when the form is genuinely unrecognized.

Acceptance is enumerated in §14.

### In scope
- **Reachability gate:** relax `POST /api/forms/upload` so a non-empty `form_type` that is **not**
  in the template registry is accepted as "infer it" (Decision 4), instead of the Phase-3 `422
  UNKNOWN_FORM_TYPE`. Store the free-text label for history/metrics.
- **Agent — schema branch:** `agent/graph.py`'s `form_schema` node gains a template-vs-inference
  branch (§6.2). Inference calls an injected **field detector** (Document AI) and an injected
  **label mapper** (LLM) to synthesize the same `TemplateField`-shaped `field_specs` the
  template path emits — so `profile_lookup` → `document_verification` → `confidence_scorer` run
  **unchanged**.
- **Agent — new tool:** `agent/tools/field_mapping_tool.py` — turns Document-AI-detected fields +
  an LLM label→canonical-key mapping into synthesized field specs (profile_key, discrete mapping
  **tier**, derived `high_stakes`, normalized bbox placement). This is the net-new "semantic field
  matching" the PRD calls the differentiator — **not** an extension of the exact-`profile_key`
  `profile_lookup_tool` (§6.3).
- **Field detection (finish the Phase-3 stub):** wire `services/form_placement/document_ai.py`
  `detect_fields()` to the real **Google Document AI Form Parser** (GCP service-account auth,
  transient/terminal error split, normalized value-region boxes; §6.4).
- **Label mapping LLM call:** add `map_field_labels(labels, canonical_keys)` to the Gemini provider
  — a strict-JSON, batch, **tier-returning** label→key mapping (§6.5). No pixel coordinates ever
  asked of the vision-LLM (that's Document AI's job).
- **Scorer:** extend `confidence_scorer_tool` to (a) **cap** an inferred field's confidence by its
  mapping tier, and (b) flag **every** inferred field `inferred_mapping` → mandatory review, at a
  new precedence slot (Decisions 1/3, §6.6).
- **DB:** migration `0005_schema_inference` — `Form.schema_source` (`template` | `inferred`) and
  `FormField.placement` (per-field normalized bbox for inferred forms; §4).
- **Renderer:** `services/form_renderer.py` gains an **inferred placement source** — per-field
  normalized bbox (from `FormField.placement`) instead of a template's `(x, y)`; the appended
  "Additional fields" fallback (Phase 3 §8.4.2) is reused unchanged for undetected/low-confidence
  boxes (§7).
- **Worker:** `fill_form_task` injects the `field_detector` + `label_mapper` callables, writes
  `Form.schema_source`, and persists `FormField.placement`; Document-AI transient errors retry,
  terminal ones (or **zero** detected fields) → `failed` (Decision 5).
- **Frontend:** an **"Other / not listed"** upload option that sends a free-text `form_type`; an
  **informational "inferred form" banner** on the review page (reusing the Phase-3 placement-warning
  banner pattern) so the user knows why more fields than usual need review. The review page itself
  needs no new logic — it is already generic over `FormFieldReviewOut`.

### Out of scope (defer)
- **Auto-submit** — never, any phase (FR7).
- **Schema promotion / learning:** a successfully-inferred schema is **not** cached or promoted
  into a reusable template. Inference runs **fresh every upload** (Decision 6). Keeps scope tight;
  the schema-inference-success-rate metric is still fully measurable.
- **History / reuse dashboard** and the **deletion cascade** — **Phase 5** (the free-text label
  stored here is what that history will surface).
- **Metrics dashboards** — **Phase 6.** Phase 4 makes `schema_source` and per-field mapping outcome
  **recoverable** (§13), not dashboarded.
- **Vision-LLM coordinate detection** — permanently rejected (PRD §10). Boxes come from Document AI.
- **Deskew / auto-rotate**, table/grid reflow, multi-column typography — unchanged from Phase 3
  (warn, don't correct; degrade to the appended page).
- **High-stakes detection for unmapped (`no_mapping`) inferred fields** — an inferred field with no
  canonical key has no reliable money/legal signal, so it is not marked `high_stakes` (it is already
  unconditionally reviewed as `no_mapping`; Decision 1 makes the point moot). Only the matched
  canonical key drives `high_stakes` on inferred forms (§6.3).

---

## 2. Decisions carried from the interview (binding for Phase 4)

| # | Area | Decision |
|---|---|---|
| 1 | **Inferred review gate** | **Every inferred-schema field always routes to review**, regardless of confidence or verification. Auto-fill still *pre-populates* the value for the reviewer, but **nothing on an inferred form is ever auto-approved** — auto-approve stays a known-template-only privilege. Rationale: `document_verification` checks whether a value appears on the *source ID doc*, so a **mis-mapped** field (e.g. detected "Guardian's Name" wrongly mapped to `full_name`) still verifies **true** — the value is on the Aadhaar, it is just wrong for *this* form. Verification cannot catch a mapping error; mandatory review is the only backstop. Implemented as a new `inferred_mapping` flag (true for all fields on an `inferred` form) feeding `needs_review`. |
| 2 | **Confident known-type override** | If the user uploads an unseen `form_type` but `classify_form` **confidently** recognizes it as an existing template type, the **template path wins** (better deterministic placement + hand-authored `high_stakes`). Inference runs **only** when `classify_form` returns `"unknown"`. Reuses the Phase-2 detection signal; never infers a form we already have a template for. This is **not** a `type_mismatch` (the user declared an unknown label, not a *conflicting known* one) — we silently adopt the detected template. |
| 3 | **Mapping confidence = discrete tiers → fixed caps** | The label mapper returns a discrete **tier** per detected label (`exact` \| `strong` \| `weak` \| `none`), each mapped to a **fixed, config-driven confidence cap** (`map_cap_exact=0.85`, `map_cap_strong=0.70`, `map_cap_weak=0.50`; `none` → `no_mapping`). The final field confidence is `min(scorer_confidence, tier_cap)`. Deterministic and unit-testable — **not** a raw self-reported LLM float (CLAUDE.md forbids leaning on that alone). The *value* is still independently grounded by `document_verification`; the tier only **caps** the score. |
| 4 | **Upload contract = any free-text unknown type** | Accept **any** non-empty `form_type`. In the registry → template path. Not in the registry → inference path, and the label is **stored verbatim** (stripped, whitespace-collapsed, ≤64 chars) on `Form.declared_form_type` for history/metrics. Empty/whitespace-only → `422`. No reserved sentinel. |
| 5 | **Failure status = reuse `failed`** | Document AI **transient** errors (429/5xx/timeout/network) retry with capped backoff like Gemini; a **terminal** Document AI failure **or zero detected form fields** lands the form in the existing **`failed`** status with a safe, non-PII `fill_error`. **No** new lifecycle status — reuses Phase-1/3 failure plumbing and the existing frontend handling. |
| 6 | **No schema promotion** | Inference runs fresh on every upload. A successfully-inferred schema is **not** persisted as a reusable template or fingerprint-cached. (Scope; revisit in a later phase if the inference metric justifies it.) |
| 7 | **Placement storage = per-field on `FormField`** | Inferred placement lives in a nullable `FormField.placement` JSON column (`{"page": int, "bbox": [x0,y0,x1,y1]}`, **normalized 0–1** fractions of the page). `NULL` for template forms (the renderer uses the template JSON) **and** for an inferred field whose box was undetected/low-confidence (→ appended page). No re-call of Document AI at download time. |
| 8 | **Bbox placement = value region + unplaced fallback** | Place the value into the detected **value region** box. If a field's detection confidence is below `documentai_min_confidence` **or** no value region was found, its `placement` is stored `NULL` → the renderer routes it to the appended "Additional fields" page rather than risk on-form misplacement (reuses the Phase-3 unplaced-page safety net). |
| 9 | **Unmapped detected fields = include as `no_mapping`** | **Every** Document-AI-detected field becomes a `FormField`. A field whose label maps to no canonical key (tier `none`) is `profile_key=NULL` → `no_mapping` (blank, always outstanding), so the user hand-fills it in review (`correct`/`approve_blank`) and the downloaded form is **complete**. Consistent with template forms, where e.g. `annual_income` is already a `no_mapping` field. |
| 10 | **Inferred field format = `as_is`** | An inferred form declares no per-field format (there is no template author). Inferred fields fill with the canonical profile value **verbatim** (`format="as_is"` — ISO date, normalized Aadhaar, etc.). Since **every** inferred field is reviewed (Decision 1), the reviewer adjusts presentation if the specific form needs a particular format; `document_verification`'s date/ID **semantic** tier still matches an ISO/normalized value against a differently-formatted source snippet (§6.6). |
| 11 | **Spec location** | This file — `SPEC-PHASE4.md`. Earlier specs unchanged; PLAN's Phase 4 heading links here. |

### Default implementation choices (not interviewed; set here)
- **Both new external calls are injected callables**, exactly like Phase 2/3's
  `classifier`/`verifier`: the worker injects `field_detector` (Document AI) and `label_mapper`
  (Gemini) via `config["configurable"]`. Graph nodes/tools stay pure, DB- and crypto-free, testable
  with fakes; **no real Document AI / Gemini call in CI** (§12).
- **`inferred_mapping` review-reason precedence:** `missing` > `verification_failed` >
  **`inferred_mapping`** > `high_stakes` > `unverified_source` > `low_confidence`. On an inferred
  form the dominant caveat is that the *mapping itself* is an unverifiable inference — a stronger
  caution than the (also-recorded) high-stakes flag, but weaker than an outright verification failure
  or a missing value the user must supply. Template forms never set `inferred_mapping`, so their
  Phase-3 precedence is **unchanged**.
- **Batch label mapping (one LLM call per form)**: all detected labels are mapped in a single
  Gemini call for latency/cost, returning a per-label `{profile_key, tier}` object.
- **Normalized (0–1) bbox coordinates** end-to-end: Document AI's normalized vertices are stored
  as page-fraction boxes and scaled by the renderer against the *actual* page rect — DPI- and
  page-size-independent by construction (no `reference_page_size` needed on the inferred path).
- **Document AI runs on the resolved-inferred type only** — never on a template-path form (Decision
  2 short-circuits to the template before any detection) and never on a `type_mismatch`.
- **DB access / IDs / timestamps:** same as Phases 0–3 — sync SQLAlchemy 2.0, `psycopg` v3, UUIDv4
  PKs, `TIMESTAMP WITH TIME ZONE` UTC with `now()` server defaults.

---

## 3. The core new capability: semantic field mapping (`field_mapping_tool`)

This is the PRD's stated differentiator — *semantic field matching over unstructured paperwork with
no fixed schema*. It is **net-new code**, deliberately **not** an extension of
`profile_lookup_tool` (which only does exact, human-pre-declared `profile_key` lookup from a
template JSON and has zero semantic capability).

### 3.1 The canonical target vocabulary
Mapping targets are exactly `form_schema_tool.CANONICAL_PROFILE_KEYS` (`full_name`, `father_name`,
`dob`, `gender`, `address`, `aadhaar_number`, `pan_number`) — the same vocabulary templates draw
from, so an inferred field is fully interchangeable with a template field downstream. A new
`form_schema_tool.HIGH_STAKES_PROFILE_KEYS = {"dob", "aadhaar_number", "pan_number"}` drives
`high_stakes` derivation for inferred fields (Decision from PLAN: dates/IDs are always high-stakes
regardless of source).

### 3.2 Inputs
- `detected: list[DetectedField]` from the injected `field_detector` (Document AI; §6.4). Each has
  `name` (the detected label text — the *key*), `page` (1-based), `value_bbox` (normalized 0–1
  tuple of the fill target region, or `None`), and `confidence`.
- `label_mapper: Callable[[list[str], list[str]], dict[str, dict]]` (injected; §6.5) — maps every
  detected label to `{"profile_key": str | None, "tier": "exact"|"strong"|"weak"|"none"}`.

### 3.3 Algorithm (`infer_schema(detected, label_mapper) -> list[TemplateField]`)
```
labels  = [d.name for d in detected]
mapping = label_mapper(labels, sorted(CANONICAL_PROFILE_KEYS))   # one batched LLM call

specs = []
for d in detected:
    m    = mapping.get(d.name, {})
    key  = m.get("profile_key")
    tier = m.get("tier", "none")
    if key not in CANONICAL_PROFILE_KEYS:      # unknown/None -> treat as no mapping
        key, tier = None, "none"

    placement = None
    if d.value_bbox is not None and d.confidence >= settings.documentai_min_confidence:
        placement = {"page": d.page, "bbox": list(d.value_bbox)}   # normalized 0-1

    specs.append(TemplateField(
        name        = _slug(d.name),            # form's own field name (stable, deduped)
        profile_key = key,                       # None -> no_mapping downstream
        high_stakes = key in HIGH_STAKES_PROFILE_KEYS,
        format      = "as_is",                   # Decision 10
        placement   = placement,                 # normalized bbox OR None (-> appended page)
        mapping_tier= None if key is None else tier,
        mapping_cap = None if key is None else _tier_cap(tier),   # §6.6 caps the score
    ))
return specs
```
- `_slug(label)` normalizes a detected label into a stable `field_name` (lowercase, non-alnum → `_`,
  truncate ≤64) and **de-duplicates** collisions with a numeric suffix so
  `FormField.uq_form_field_name (form_id, field_name)` never conflicts (two detected "Name" rows →
  `name`, `name_2`). The original label is preserved for the reviewer via the appended-page label and
  the field display.
- **Duplicate canonical mappings are allowed:** two labels both mapping to `full_name` both fill from
  the same profile value and are both reviewed. No dedup on `profile_key` — a form legitimately asks
  for the same datum twice (applicant + declaration).
- `_tier_cap` reads `settings.map_cap_exact/_strong/_weak` (Decision 3).

`TemplateField` (in `form_schema_tool`) gains two **optional** fields defaulting to `None`
(`mapping_tier`, `mapping_cap`) so template loading and every existing test are untouched; a
template field simply has `mapping_cap=None`, which the scorer reads as "no cap".

---

## 4. Data Model changes

One Phase-4 migration `0005_schema_inference`. Both models are already imported by `db/base.py`.

### 4.1 `forms` — addition
| Column | Type | Notes |
|---|---|---|
| `schema_source` | text, not null, default `"template"` | `"template"` (a registry template drove the fill — includes the Decision-2 confident-detection-override case) \| `"inferred"` (Document AI + semantic mapping). Read by the renderer (§7), the review projection (§8), and the Phase-6 schema-inference-success-rate metric. Existing rows back-fill `"template"`. |

`status` is unchanged — inference reuses `pending → processing → (in_review | approved | failed |
type_mismatch)` (Decision 5; a zero-flag `approved` is unreachable on an inferred form because
Decision 1 flags **every** field, so an inferred form always lands `in_review` — same practical
invariant Phase 3 noted for real templates).

### 4.2 `form_fields` — addition
| Column | Type | Notes |
|---|---|---|
| `placement` | JSON, nullable | Per-field normalized placement for an **inferred** field: `{"page": int, "bbox": [x0,y0,x1,y1]}` with `bbox` in 0–1 page fractions (Decision 7/8). `NULL` for template fields (renderer uses the template JSON) and for an inferred field whose box was undetected/low-confidence (→ appended page). |

No other `form_fields` change — `profile_key`, `confidence`, `high_stakes`, `verified`,
`review_*`, `flags`, etc. carry inferred fields exactly as they carry template fields.

`flags` (existing JSON) gains an `"inferred_mapping"` key in its recorded audit dict (§6.6). No
migration — it is a schema-less JSON column.

---

## 5. Config, deps & env additions

`config.py` — reuse all Phase-1/2/3 knobs. Add:
```python
# Schema inference (Phase 4) — inferred fields default to LOWER confidence (CLAUDE.md/PRD).
# Discrete label-mapping tiers -> fixed caps on the final field confidence (Decision 3).
map_cap_exact: float = 0.85      # detected label is an exact synonym of a canonical key
map_cap_strong: float = 0.70     # strong/plausible match
map_cap_weak: float = 0.50       # weak/uncertain match ("none" -> no_mapping, no cap)

# Document AI Form Parser (Phase 4-activated). documentai_location / documentai_processor_id
# already exist from Phase 3; add the project + a box-confidence gate.
documentai_project_id: str = ""          # GCP project owning the Form Parser processor
documentai_min_confidence: float = 0.5   # below this, a detected box -> appended "unplaced" page
```
`documentai_location` (`"us"`) and `documentai_processor_id` (`""`) already exist from Phase 3.

**Dependencies (`backend/pyproject.toml`):** `google-cloud-documentai` was **listed but
Phase-4-activated** in Phase 3 — it is now **imported** on the inference path. No other new backend
dep (PyMuPDF/OpenCV/Gemini all already present). No new frontend dep.

**Auth (`.env.example`):** Document AI uses **GCP service-account** auth, **not** the Gemini API key.
Add commented guidance:
```
# Document AI (Phase 4) — service-account auth, NOT the Gemini API key.
# GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
# DOCUMENTAI_PROJECT_ID=your-gcp-project
# DOCUMENTAI_LOCATION=us
# DOCUMENTAI_PROCESSOR_ID=your-form-parser-processor-id
# DOCUMENTAI_MIN_CONFIDENCE=0.5
# MAP_CAP_EXACT=0.85  MAP_CAP_STRONG=0.70  MAP_CAP_WEAK=0.50
```
The container/host must have `GOOGLE_APPLICATION_CREDENTIALS` pointing at a mounted service-account
JSON for the real call; absent it (or an empty `documentai_processor_id`), `detect_fields` raises a
clear terminal `DocumentAIError` → the fill lands `failed` (never a silent pass). "Same GCP project"
means shared *billing* only — this is a different auth mechanism from Gemini, provisioned here.

---

## 6. Agent pipeline changes

### 6.1 `agent/state.py` — extend the field dicts
- Intermediate lookup dict (comment) and `FieldResult` gain: `mapping_tier: str | None`,
  `placement: dict | None`. `FieldResult.flags` documents the new `inferred_mapping` key.
- A top-level `schema_source: str` is added to `AgentState` (`"template"` | `"inferred"`), set by
  the `form_schema` node and read by the worker (§6.7).

### 6.2 `agent/graph.py` — template-vs-inference branch in `form_schema`
The graph **shape** is unchanged (`form_schema → profile_lookup → document_verification →
confidence_scorer`, with the `type_mismatch → END` branch). Only `_form_schema_node` grows a branch;
`profile_lookup`/`document_verification`/`confidence_scorer` are **untouched** — they consume
synthesized inferred specs identically to template specs.

```
_form_schema_node(state, config):
    cfg      = config["configurable"]
    declared = state["declared_form_type"]
    detected = cfg["classifier"](cfg["images"], known_types())     # Gemini, as Phase 2/3
    known    = set(known_types())

    if declared in known:
        # Phase 2/3 behavior, unchanged: only a CONFIDENT DIFFERENT known type blocks.
        resolved, mismatch = resolve_form_type(declared, detected)
        if mismatch:
            return { ...type_mismatch=True, field_specs=[], schema_source="template" }
        specs = load_template(resolved).required_fields
        return { form_type=resolved, schema_source="template", field_specs=specs, ... }

    # declared is an UNSEEN type (free-text; Decision 4)
    if detected in known:
        # Decision 2 — confident detection wins; adopt the template, NOT a mismatch.
        specs = load_template(detected).required_fields
        return { form_type=detected, schema_source="template", field_specs=specs,
                 detected_form_type=detected, type_mismatch=False }

    # Decision 1/2 — genuinely unrecognized: INFER.
    detected_fields = cfg["field_detector"](cfg["images"])          # Document AI (injected)
    specs = field_mapping_tool.infer_schema(detected_fields, cfg["label_mapper"])  # LLM mapping
    return { form_type=declared, schema_source="inferred", field_specs=specs,
             detected_form_type="unknown", type_mismatch=False }
```
- `load_template` is **no longer called unconditionally** at the top of the node (it would raise
  `TemplateError` on an unseen type) — it now runs only inside the two template branches.
- `_route_after_schema` is unchanged (`END` on `type_mismatch`, else `profile_lookup`). An inferred
  form with **zero** detected fields yields `field_specs=[]`; the worker treats an empty inferred
  schema as a `failed` fill (Decision 5, §6.7) rather than an empty "approved" form.

### 6.3 `field_mapping_tool` (new) — §3. High-stakes derivation
`high_stakes = matched_key in HIGH_STAKES_PROFILE_KEYS`. An unmapped (`no_mapping`) inferred field
is `high_stakes=False` — it is already unconditionally reviewed (Decision 1/9), so nothing is lost.

### 6.4 `services/form_placement/document_ai.py` — the real Form Parser call (finishes the stub)
Replace the Phase-3 "not implemented" raise with a real call:
- Auth: default GCP credentials (`GOOGLE_APPLICATION_CREDENTIALS`); processor path from
  `documentai_project_id` / `documentai_location` / `documentai_processor_id`. Empty processor id →
  terminal `DocumentAIError`.
- Send each preprocessed page image (`image/jpeg`) through the Form Parser; aggregate
  `document.pages[].form_fields[]`. For each: `name` = the **field label** layout text;
  `value_bbox` = the **field value** layout's bounding box (the fill target), converted from
  Document AI **normalized vertices** to a normalized `(x0, y0, x1, y1)` tuple; `page` = 1-based
  page index; `confidence` = the field's detection confidence. A field with no usable value layout
  → `value_bbox=None`.
- **Error split** mirrors `VisionExtractionError`: `DocumentAIError(msg, transient=bool)`. Transient
  = `ServiceUnavailable`/`DeadlineExceeded`/`ResourceExhausted`(429)/`GoogleAPICallError` network
  class; terminal = `InvalidArgument`/`PermissionDenied`/`NotFound`/auth/empty-config. Never logs
  document bytes or extracted text.
- Returns `list[DetectedField]` (dataclass extended: `name`, `page`, `value_bbox`, `confidence`).

> Consult the project's provider reference before writing the Document AI client wiring; keep the
> module import-safe when the dep/creds are absent (import at call time or guard) so unrelated tests
> and app startup never require GCP credentials.

### 6.5 Vision provider — one addition (`services/ocr/vision_llm.py`)
`map_field_labels(labels: list[str], canonical_keys: list[str]) -> dict[str, dict]` — a strict-JSON,
**text-only** (no image) Gemini call using the shared `_generate_json` path (same transient/terminal
`VisionExtractionError` handling). Response schema: an object keyed by label → `{profile_key: enum
[*canonical_keys, "none"], tier: enum ["exact","strong","weak","none"]}`. Prompt frames it as
semantic matching of Indian government-form field labels to a fixed personal-data vocabulary,
explicitly instructed to answer `"none"` when no canonical key clearly fits rather than forcing a
guess (mirrors `classify_form`'s "prefer unknown over guessing"). **No pixel coordinates ever
requested** — boxes come from Document AI (§6.4).

### 6.6 `confidence_scorer_tool` — cap + `inferred_mapping` flag (extends Phase 3)
Per field, after verification annotates `verified`/`verification_method` and the (unchanged Phase-3)
`_confidence` is computed:

**Confidence cap (new):**
```python
conf = _confidence(item)                      # Phase-3 logic, unchanged
cap  = item.get("mapping_cap")                # None for template fields and no_mapping fields
if cap is not None:
    conf = min(conf, cap)                     # inferred fields default LOWER (Decision 3)
```
So a verified-**exact** inferred field that would otherwise promote to `ocr_confidence_high` (0.90)
is capped to `map_cap_exact` (0.85) → band `medium`, never `high`. A human **correction** during
review still reaches 1.0 (set directly by the review endpoint, not through the scorer), because a
person deliberately fixed it.

**Flags** gain `inferred_mapping`:
```python
flags = {
  "missing":            item["missing"],
  "verification_failed": item["missing"] is None and not item["verified"],
  "inferred_mapping":   item.get("inferred", False),      # True for ALL fields on an inferred form
  "high_stakes":        item["high_stakes"],
  "unverified_source":  item["candidate_status"] in _UNVERIFIED_STATUSES,
  "low_confidence":     conf < settings.confidence_threshold,
  "transformed":        item["transformed"],
}
needs_review = missing or verification_failed or inferred_mapping or high_stakes
               or unverified_source or low_confidence
```
**`review_reason` precedence (revised):** `missing` → `verification_failed` → **`inferred_mapping`**
→ `high_stakes` → `unverified_source` → `low_confidence`. `transformed` is still never a reason.

The `inferred` per-field boolean reaches the scorer via the lookup dict: `profile_lookup_tool`
copies `spec.mapping_cap`/`spec.mapping_tier`/`spec.placement` and sets `inferred = spec.mapping_cap
is not None or <schema_source flag>`. Because a `no_mapping` inferred field has `mapping_cap=None`,
`inferred` is carried explicitly from `schema_source` rather than inferred from the cap — the
`profile_lookup` node reads `state["schema_source"]` and stamps `inferred` on every field dict
(mapped or not), so a `no_mapping` inferred field is still flagged `inferred_mapping` (though
`missing` outranks it as the shown reason).

> **Why this is safe, not paranoid:** on a template form `inferred_mapping` is always false, so the
> Phase-3 behavior (and its full precedence table) is byte-for-byte unchanged. On an inferred form,
> *every* field is reviewed because the *mapping* is an unverifiable inference that
> `document_verification` structurally cannot catch (Decision 1). This is exactly UC3's promised
> "more fields flagged for review (expected)."

### 6.7 `fill_form_task` — inject detectors, persist inference outputs (`workers/tasks.py`)
1–2, 2b (skew), 3 (snippet snapshot). Unchanged from Phase 3.
4. **Inject two more callables** into the graph config alongside `snapshot`/`images`/`classifier`/
   `verifier`: `field_detector = document_ai.detect_fields`, `label_mapper = vision_llm.map_field_labels`.
5. `graph.invoke(...)` as before. Catch `DocumentAIError` **and** `VisionExtractionError`:
   transient → `_retry_or_fail_form` (capped backoff); terminal → `_fail_form` with a safe
   non-PII reason (`"could not detect form fields"` / `"form classification or mapping failed"`).
6. **Zero detected fields** (`schema_source == "inferred"` and `result["fields"]` empty) →
   `_fail_form("could not detect any fields on this form")` (Decision 5) — never a vacuous `approved`.
7. Write `form.schema_source = result["schema_source"]`. Persist each field via
   `_persist_form_fields`, now also writing `FormField.placement = f.get("placement")`. Terminal
   status derives from outstanding fields exactly as Phase 3 (an inferred form always has ≥1
   outstanding → `in_review`).
8. **Retries/idempotency:** unchanged posture — delete-then-insert; a transient Document-AI/Gemini
   error retries the whole fill; re-run only from a non-terminal form; PII never logged (labels,
   boxes, values, model output all stay out of logs — log by `form_id`/counts/`schema_source`).

---

## 7. Rendering the inferred placement (`services/form_renderer.py`)

Phase 3's renderer is template-driven: `render(form_type, fields, blank_bytes, content_type)` loads
the template by `form_type` for placement. For an inferred form there is **no template file**
(`load_template` would raise), and placement lives per-field on `FormField.placement`.

Changes:
- `render(...)` gains a `schema_source: str` argument (and each `RenderField` gains
  `placement: dict | None`). When `schema_source == "template"` → the **exact Phase-3 path**
  (template AcroForm → template `(x, y)` → unplaced). When `schema_source == "inferred"` → **skip
  `load_template` entirely** and place from each field's normalized bbox:
  - `page = doc[placement["page"] - 1]` (guard page range); `bbox` is normalized 0–1, scaled by the
    actual `page.rect`: `x0 = bbox[0]·page.rect.width`, etc.
  - Insert the value near the value box's baseline (`insert_text((x0 + pad, y1·H − pad), value,
    fontsize=…)`), font size derived from the box height (clamped) or the default. **Same
    coordinate-path skew caveat applies** — an inferred form is image-based, so the Phase-3
    placement-warning + "Additional fields" fallback both matter here; keep the required
    coordinate-path skew comment.
  - A field with `placement is None` (undetected/low-confidence box, Decision 8) or `value is None`
    (missing/`no_mapping`/approved-blank) → the appended **"Additional fields"** page (Phase 3
    §8.4.2), labeled with the field's (human-readable) name. Nothing is silently dropped.
- **AcroForm** is not attempted on the inferred path (an unseen scanned form has no named widgets;
  if it *did* have them it would typically be classified/templated). The watermark stamp is
  unchanged.

The download endpoint (§8) passes `form.schema_source` and each row's `placement` into `render`.

---

## 8. API changes (`api/routes/forms.py`, `schemas/form.py`)

### 8.1 `POST /api/forms/upload` — relax the gate (Decision 4)
```python
form_type = (form_type or "").strip()
if not form_type:
    raise _err(422, "Form type is required", "MISSING_FORM_TYPE")
if len(form_type) > 64:
    form_type = form_type[:64]
# NO registry membership check anymore — an unknown type triggers inference downstream.
```
The old `UNKNOWN_FORM_TYPE` 422 is **removed**. Everything else (content-type/size checks, S3 put,
`fill_form_task.delay`) is unchanged. `known_types()` is no longer imported here.

### 8.2 Review/read projections — surface `schema_source`
- `FormReviewOut` and `FormOut` gain `schema_source: str`. `get_form`, `get_form_review`, and the
  `ReviewActionResponse` path populate it from `form.schema_source`. The frontend uses it to render
  the informational "inferred form" banner (§9). No other contract change — `FormFieldReviewOut` is
  already generic and carries the new `review_reason` value `"inferred_mapping"` as a plain string.
- `get_form`/`get_form_review`'s existing `try/except` around `load_template(...).display_name`
  already degrades to `form.declared_form_type` for an inferred type (which has no template) — the
  free-text label is shown as-is. No change needed there.

### 8.3 `GET /api/forms/{id}/download` — inferred placement
Build `RenderField`s with `placement=row.placement` and call
`render(form.declared_form_type, render_fields, blank_bytes, content_type, schema_source=form.schema_source)`.
The download gate (`409` unless `approved`), lazy render + `rendered_s3_key` cache, and cache
invalidation on review edits are **all unchanged** (Phase 3). An inferred form only reaches
`approved` after the user reviews every field (Decision 1), so the gate does real work here.

Cross-user `404`, no-auto-submit, and masked-vs-full-PDF boundaries are unchanged from Phase 3.

---

## 9. Frontend (`frontend/src`)

- **Upload page (`pages/FormFill.tsx` / upload control):** add an **"Other / not listed"** choice to
  the form-type selector that reveals a **free-text input**; its value is sent as `form_type`
  (Decision 4). Client-side: non-empty, trimmed, ≤64 chars. Known types still use the existing
  dropdown → template path.
- **Review page (`pages/Review.tsx`):** when `schema_source === "inferred"`, show an
  **informational banner** (reusing the Phase-3 placement-warning banner component/pattern, distinct
  copy, non-alarming): *"This form wasn't in our library, so we detected its fields automatically.
  Every field is shown for your review — please check each value and its placement before
  downloading."* The per-field UI is **unchanged** — `ConfidenceField` already renders any
  `review_reason`; add a label/tooltip mapping for the new `"inferred_mapping"` reason (yellow band:
  "auto-matched field — please confirm"). No new approve/correct/download logic.
- **`types.ts`:** add `schema_source` to the form review/detail types; add `"inferred_mapping"` to
  the known `review_reason` union; add the free-text upload option to the form-type input type.
- **`api/client.ts`:** no new endpoints — `uploadForm` just now accepts an arbitrary `form_type`
  string. `getFormReview`/`downloadForm` are unchanged.

---

## 10. Security & edge cases (must-handle)
- **Mapping is never trusted to auto-approve** (Decision 1): every inferred field is
  `needs_review`; download stays gated behind full human review. A confident **mis-mapping** that
  verifies against the source doc is caught only by the human, by design.
- **Confidence genuinely discounted** (Decision 3): the tier cap means an inferred field can never
  present as `high` band, matching CLAUDE.md/PRD's "inferred schemas default to lower confidence."
- **No LLM pixel coordinates** (PRD §10): boxes come from Document AI; the vision-LLM is asked only
  for a semantic label→key **tier**, never a coordinate.
- **No raw PII / no document text in logs** (CLAUDE.md): detected labels, boxes, mapped values,
  model output, and Document-AI responses are never logged. Log by `form_id`/counts/`schema_source`.
  `fill_error` stays a fixed safe string.
- **Field-name collisions handled** (§3.3): `_slug` + numeric-suffix dedup guarantees
  `uq_form_field_name (form_id, field_name)` never conflicts across two detected fields sharing a
  label; delete-then-insert idempotency is preserved.
- **Free-text `form_type` sanitized** (Decision 4): stripped, whitespace-collapsed, length-capped;
  stored verbatim otherwise (it is a user-facing label, not an identifier — it never selects a
  code path beyond "known vs infer").
- **Failure never silently passes** (Decision 5): a terminal Document-AI error, missing GCP creds,
  empty processor config, or **zero** detected fields all land the form `failed` with a safe reason —
  never a vacuous `approved`/empty draft. Transient errors retry with capped backoff.
- **Placement gate is a safety net, not a guess** (Decision 8): a low-confidence/absent box routes
  its field to the appended "Additional fields" page rather than stamping text at a shaky
  coordinate; the value is still reviewable and present in the PDF.
- **Ownership scoping / no auto-submit / masking boundary / at-rest encryption / verification never
  laundering trust:** all unchanged from Phase 3 and apply identically to inferred forms.
- **Deferred hardening (noted, not built):** schema promotion/caching (Decision 6), per-user Document
  AI rate/cost limiting, value-region vs. label-region disambiguation beyond Form Parser's own output,
  and multi-column/table reflow.

---

## 11. File-by-file change list

**Backend — new:**
`agent/tools/field_mapping_tool.py` (`infer_schema` + `_slug` + `_tier_cap`; §3),
`db/migrations/versions/0005_schema_inference.py` (`Form.schema_source`, `FormField.placement`).

**Backend — implement (finish Phase-3 stubs):**
`services/form_placement/document_ai.py` (real Form Parser call; extend `DetectedField` with
`page`/`value_bbox`; transient/terminal `DocumentAIError`; §6.4).

**Backend — edit:**
`agent/graph.py` (`_form_schema_node` template-vs-inference branch; §6.2),
`agent/state.py` (`schema_source` on `AgentState`; `mapping_tier`/`placement` on the field dicts +
`inferred_mapping` in the `flags` comment),
`agent/tools/form_schema_tool.py` (add `HIGH_STAKES_PROFILE_KEYS`; add optional
`mapping_tier`/`mapping_cap` to `TemplateField`),
`agent/tools/profile_lookup_tool.py` (copy `mapping_cap`/`mapping_tier`/`placement`; stamp
`inferred` from `schema_source` onto every field dict, mapped or not),
`agent/tools/confidence_scorer_tool.py` (cap by `mapping_cap`; `inferred_mapping` flag + precedence;
§6.6),
`services/ocr/vision_llm.py` (add `map_field_labels`; §6.5),
`workers/tasks.py` (`fill_form_task`: inject `field_detector`/`label_mapper`, catch
`DocumentAIError`, zero-field → `failed`, write `schema_source`, persist `placement`; §6.7),
`services/form_renderer.py` (`schema_source` arg + `RenderField.placement`; inferred normalized-bbox
placement path; §7),
`models/form.py` (`Form.schema_source`; `FormField.placement`),
`api/routes/forms.py` (relax upload gate; pass `schema_source`/`placement` to `render`; drop the
`known_types` import/`UNKNOWN_FORM_TYPE` 422),
`schemas/form.py` (`schema_source` on `FormOut`/`FormReviewOut`),
`config.py` (`map_cap_*`, `documentai_project_id`, `documentai_min_confidence`),
`pyproject.toml` (activate `google-cloud-documentai` — already listed),
`.env.example` (Document AI service-account guidance).

**Frontend — edit:**
`pages/FormFill.tsx` (free-text "Other" form-type option), `pages/Review.tsx` (inferred-form
informational banner), `components/ConfidenceField.tsx` (label/tooltip for `inferred_mapping`),
`types.ts` (`schema_source`, `inferred_mapping`), `api/client.ts` (arbitrary `form_type` string).

**Docs — edit:**
`README.md` — document the inference path (Document AI detection, semantic mapping, why **every**
inferred field is reviewed, the fresh-inference/no-promotion posture) and the Document AI
service-account setup.

**Untouched this phase:** `document_verification_tool` and the review/approve/download endpoints'
core logic (generic over template vs inferred by design — PLAN), `services/ocr/tesseract.py`,
`metrics/` (Phase 6), the history router + `DELETE /api/profile` (Phase 5).

---

## 12. Testing (`backend/tests`, `frontend`)

**Backend (pytest):**
- **`field_mapping_tool` (pure, stubbed `label_mapper`):** exact/strong/weak/none tiers → correct
  `profile_key`/`high_stakes`/`mapping_cap`; `none` → `no_mapping` (`profile_key=None`, no cap);
  `high_stakes` set only for `dob`/`aadhaar_number`/`pan_number`; a low-confidence or `None`
  `value_bbox` → `placement=None`; `_slug` dedup on colliding labels (`name`, `name_2`); two labels
  → same canonical key both kept.
- **`document_ai.detect_fields` (mocked client):** normalized-vertex → normalized-bbox conversion;
  label vs value region selection; transient vs terminal `DocumentAIError` classification; empty
  processor config → terminal; **no** real network call. (Real call never exercised in CI.)
- **`map_field_labels` (mocked `_generate_json`):** returns the per-label tier dict; `"none"` on no
  fit; transient/terminal `VisionExtractionError` split via the shared path.
- **`confidence_scorer_tool` (pure):** inferred exact-verified is **capped** to `map_cap_exact`
  (band `medium`, not `high`); `inferred_mapping` flags every inferred field incl. a verified
  non-high-stakes one → `needs_review`; precedence `missing > verification_failed > inferred_mapping
  > high_stakes > unverified_source > low_confidence`; a template field
  (`mapping_cap=None`, `inferred=False`) scores **identically to Phase 3** (regression) — no cap, no
  new flag.
- **graph (`build_graph`) with fakes:** declared-known → template path (unchanged); declared-unknown
  + confident detection → template path with `schema_source="template"`, no `type_mismatch`
  (Decision 2); declared-unknown + `"unknown"` detection → inference path, `schema_source="inferred"`,
  synthesized specs from stub `field_detector`/`label_mapper`; empty detection → empty `field_specs`.
- **`fill_form_task`:** inferred happy path persists `schema_source="inferred"`,
  `placement` JSON on placed fields, `NULL` on low-confidence ones, and lands `in_review` with every
  field outstanding; a `DocumentAIError(transient=True)` retries; terminal → `failed`; **zero
  detected fields → `failed`** (not `approved`); idempotent re-run; PII/labels/boxes never logged.
- **upload gate:** an unknown `form_type` is **accepted** (`202`, no more `UNKNOWN_FORM_TYPE`);
  empty/whitespace → `422`; an over-long label is truncated; a **known** type still routes to the
  template path unchanged.
- **renderer (`schema_source="inferred"`):** normalized bbox scaled to the actual page → text placed;
  a `placement=None` field → appended "Additional fields" page; a `value=None` field omitted; does
  **not** call `load_template`; template path (`schema_source="template"`) byte-for-byte unchanged
  (regression).
- **review/download (reused):** an inferred form's download `409`s until every field reviewed, then
  renders via the inferred placement path; masking/at-rest/cross-user all hold (regression over
  Phase 3 with an inferred fixture).
- **migration:** `0005` applies; `forms.schema_source` defaults `"template"` and back-fills existing
  rows; `form_fields.placement` is nullable JSON.

**Frontend (vitest, light):** the "Other" upload option reveals a text input and submits the
free-text `form_type`; the Review page shows the inferred-form banner when `schema_source ===
"inferred"` and hides it otherwise; `inferred_mapping` renders its yellow band + label; the download
button stays disabled until `approved` on an inferred form.

---

## 13. Metrics seam (full instrumentation is Phase 6)
Phase 4 makes these **recoverable**, not dashboarded: **schema-inference success rate**
(`Form.schema_source == "inferred"` fills that reached `in_review`/`approved` vs. `failed`), the
**mapping-tier distribution** (`mapping_tier` across inferred `FormField`s — how confidently labels
matched), and **inferred-form review burden** (outstanding-field count vs. template forms, evidencing
UC3's "more fields flagged, expected"). No `metrics/` code this phase.

---

## 14. Acceptance checklist (Done-When, enumerated)
1. `POST /forms/upload` **accepts** a non-empty `form_type` not in the registry (no more
   `UNKNOWN_FORM_TYPE` 422); empty/whitespace → `422`; the free-text label is stored on the `Form`.
2. Uploading an unseen form the vision-LLM **confidently** recognizes as a known type fills it from
   the **template** (`schema_source="template"`, template placement + hand-authored high-stakes),
   **not** via inference, and it is **not** a `type_mismatch`.
3. Uploading a genuinely unrecognized form (`classify_form` → `"unknown"`) runs **Document AI**
   detection → **LLM semantic mapping** → synthesized specs → the **same** profile-lookup →
   verification → scorer pipeline, landing `in_review` (`schema_source="inferred"`).
4. **Every** field on an inferred form is `needs_review` (`inferred_mapping`), regardless of
   verification/confidence — nothing on an inferred form auto-approves; download is gated behind full
   review (`409` until every field resolved).
5. An inferred field's confidence is **capped** by its mapping tier (`exact/strong/weak` →
   `0.85/0.70/0.50`), so it never presents as `high` band; a `none`-tier label → `no_mapping`
   (blank, always outstanding), hand-fillable in review; the downloaded form is complete.
6. `dob`/`aadhaar_number`/`pan_number` inferred fields are `high_stakes`; other mapped inferred
   fields are not; duplicate labels mapping to the same canonical key both fill and both review.
7. A **mis-mapped** field that (correctly) verifies against the source ID doc is still routed to
   review — the human is the backstop the verifier structurally can't be (Decision 1).
8. `GET /download` on an approved inferred form renders each value at its **detected normalized
   bbox** (scaled to the actual page); a low-confidence/undetected box → the appended "Additional
   fields" page (nothing dropped); the watermark, the `rendered_s3_key` cache, and cache
   invalidation on edits all behave as Phase 3; **nothing is ever submitted** (FR7).
9. A terminal Document AI failure, missing/empty GCP processor config, or **zero** detected fields →
   the form lands **`failed`** with a safe non-PII reason (never a vacuous `approved`); transient
   errors retry with capped backoff.
10. The review UI shows an **informational "inferred form" banner** when `schema_source ==
    "inferred"`; the per-field review/approve/correct/approve-blank/download flow is otherwise the
    Phase-3 flow unchanged.
11. **Regression:** a **template** form scores, flags, places, and downloads **exactly** as in Phase
    3 — `mapping_cap=None`/`inferred=False` leave the scorer, renderer, and precedence table
    byte-for-byte unchanged.
12. At-rest ciphertext, masked-vs-full-PDF boundary, cross-user `404`, and a zero-PII log sweep
    (labels/boxes/values/model output all absent) hold for inferred forms just as for template forms.
13. `0005` migration applies (`Form.schema_source` default `"template"` back-fills; `FormField.
    placement` nullable JSON); `ruff`/`mypy` clean; new backend tests + light frontend tests green;
    **no real Document AI / Gemini network call in CI** (per `memory/dev-environment`).
```
