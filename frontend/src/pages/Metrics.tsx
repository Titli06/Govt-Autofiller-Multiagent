// Metrics dashboard (Phase 6, PRD §9): per-user AGGREGATES only — per-form detail
// stays on the History page (SPEC-PHASE6.md Decision 2), so nothing here duplicates
// that projection. Ratios render "n/a" when their denominator is 0, never a fake 0%.

import { useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { MetricsOut } from "../types";
import { formatDuration, formatRatio, formatSeconds } from "../utils/format";

const STATUS_LABEL: Partial<Record<string, string>> = {
  approved: "Approved",
  in_review: "Needs review",
  failed: "Failed",
  type_mismatch: "Type mismatch",
};

const TIER_LABEL: Partial<Record<string, string>> = {
  exact: "Exact",
  strong: "Strong",
  weak: "Weak",
  none: "No mapping",
};

function StatCard({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="metric-card">
      <div className="metric-card-label">{label}</div>
      <div className="metric-card-value">{value}</div>
      {detail && <div className="metric-card-detail">{detail}</div>}
    </div>
  );
}

export default function Metrics() {
  const [metrics, setMetrics] = useState<MetricsOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getMetrics()
      .then(setMetrics)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load metrics"));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (metrics === null) return <p>Loading…</p>;

  if (metrics.forms_total === 0) {
    return (
      <div>
        <h1>Metrics</h1>
        <p>No forms yet — metrics will appear here once you fill your first form.</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Metrics</h1>

      <section className="metric-grid">
        <StatCard label="Forms filled" value={String(metrics.forms_total)} />
        <StatCard
          label="Auto-filled at high confidence"
          value={formatRatio(metrics.autofill_rate)}
          detail={`${metrics.autofilled_fields} of ${metrics.total_fields} fields`}
        />
        <StatCard label="High-confidence share" value={formatRatio(metrics.high_confidence_rate)} />
        <StatCard label="Verification pass rate" value={formatRatio(metrics.verification_pass_rate)} />
        <StatCard
          label="Accuracy (approved-as-is proxy)"
          value={formatRatio(metrics.accuracy_proxy)}
          detail="Correction-rate proxy, not ground truth"
        />
      </section>

      <h2>Latency</h2>
      <section className="metric-grid">
        <StatCard label="Avg. time to filled draft" value={formatDuration(metrics.avg_fill_latency_ms)} />
        <StatCard label="Avg. review time" value={formatDuration(metrics.avg_review_latency_ms)} />
        <StatCard label="Avg. document OCR time" value={formatDuration(metrics.avg_ocr_latency_ms)} />
      </section>

      <h2>Time saved (estimate)</h2>
      <section className="metric-grid">
        <StatCard
          label="Estimated manual fill time"
          value={formatSeconds(metrics.estimated_manual_seconds)}
          detail={`${metrics.manual_seconds_per_field}s/field × ${metrics.total_fields} fields — an estimate, not a measurement`}
        />
        <StatCard label="Measured review time" value={formatSeconds(metrics.measured_review_seconds)} />
        <StatCard
          label="Estimated time saved"
          value={formatSeconds(metrics.estimated_time_saved_seconds)}
        />
      </section>

      <h2>Schema inference</h2>
      <section className="metric-grid">
        <StatCard label="Inferred forms" value={String(metrics.inferred_forms_total)} />
        <StatCard
          label="Schema-inference success rate"
          value={formatRatio(metrics.schema_inference_success_rate)}
        />
      </section>

      {Object.keys(metrics.mapping_tier_distribution).length > 0 && (
        <>
          <h3>Mapping-tier distribution</h3>
          <ul>
            {Object.entries(metrics.mapping_tier_distribution).map(([tier, count]) => (
              <li key={tier}>
                {TIER_LABEL[tier] ?? tier}: {count}
              </li>
            ))}
          </ul>
        </>
      )}

      <h2>By status</h2>
      <ul>
        {Object.entries(metrics.forms_by_status).map(([status, count]) => (
          <li key={status}>
            {STATUS_LABEL[status] ?? status}: {count}
          </li>
        ))}
      </ul>
    </div>
  );
}
