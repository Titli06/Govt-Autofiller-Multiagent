"""GET /api/metrics response contract (Phase 6, PRD §9). Per-user aggregate
projection only — counts, averages (ms), and ratios (0..1, or null when the
denominator is 0). No decryption, no field values, no PII (SPEC-PHASE6.md §5.2).
"""

from __future__ import annotations

from pydantic import BaseModel


class MetricsOut(BaseModel):
    forms_total: int
    forms_by_status: dict[str, int]

    # Latency (coarse spans, Decision 4). Averages over rows with a non-null span.
    avg_fill_latency_ms: float | None
    avg_review_latency_ms: float | None
    avg_ocr_latency_ms: float | None

    # Auto-fill (PRD §9).
    total_fields: int
    autofilled_fields: int
    autofill_rate: float | None
    high_confidence_rate: float | None

    # Schema inference (PRD §9 / Phase 4).
    inferred_forms_total: int
    schema_inference_success_rate: float | None
    mapping_tier_distribution: dict[str, int]

    # Trust (Phase 3 seam).
    verification_pass_rate: float | None
    accuracy_proxy: float | None

    # Time saved (Decision 5) — manual_* is an ESTIMATE, not a measurement.
    manual_seconds_per_field: int
    estimated_manual_seconds: int
    measured_review_seconds: int
    estimated_time_saved_seconds: int

    # Reuse (UC5 denominator).
    forms_per_profile: int
