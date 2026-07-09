import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { MetricsOut } from "../types";
import Metrics from "./Metrics";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      getMetrics: vi.fn(),
    },
  };
});

afterEach(() => vi.restoreAllMocks());

function metrics(overrides: Partial<MetricsOut> = {}): MetricsOut {
  return {
    forms_total: 4,
    forms_by_status: { approved: 2, in_review: 1, failed: 1 },
    avg_fill_latency_ms: 2500,
    avg_review_latency_ms: 1500,
    avg_ocr_latency_ms: 6000,
    total_fields: 10,
    autofilled_fields: 6,
    autofill_rate: 0.6,
    high_confidence_rate: 0.5,
    inferred_forms_total: 2,
    schema_inference_success_rate: 0.5,
    mapping_tier_distribution: { exact: 1, weak: 1 },
    verification_pass_rate: 0.75,
    accuracy_proxy: 0.8,
    manual_seconds_per_field: 45,
    estimated_manual_seconds: 450,
    measured_review_seconds: 30,
    estimated_time_saved_seconds: 420,
    forms_per_profile: 4,
    ...overrides,
  };
}

describe("Metrics page", () => {
  it("shows an empty-state message when no forms have been filled", async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(metrics({ forms_total: 0 }));
    render(<Metrics />);
    expect(await screen.findByText(/no forms yet/i)).toBeDefined();
  });

  it("renders the auto-fill rate and field counts", async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(metrics());
    render(<Metrics />);

    expect(await screen.findByText("60%")).toBeDefined();
    expect(screen.getByText(/6 of 10 fields/i)).toBeDefined();
  });

  it("renders 'n/a' for a null ratio instead of a fake 0%", async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(
      metrics({ schema_inference_success_rate: null, verification_pass_rate: null }),
    );
    render(<Metrics />);

    await screen.findByText(/forms filled/i);
    const naValues = screen.getAllByText("n/a");
    expect(naValues.length).toBeGreaterThanOrEqual(2);
  });

  it("labels estimated time saved as an estimate", async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(metrics());
    render(<Metrics />);

    expect(await screen.findByText(/time saved \(estimate\)/i)).toBeDefined();
    expect(screen.getByText(/an estimate, not a measurement/i)).toBeDefined();
  });

  it("renders the mapping-tier distribution when present", async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(metrics());
    render(<Metrics />);

    await screen.findByText(/mapping-tier distribution/i);
    expect(screen.getByText(/exact: 1/i)).toBeDefined();
    expect(screen.getByText(/weak: 1/i)).toBeDefined();
  });

  it("shows an error message when metrics fail to load", async () => {
    const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
    vi.mocked(api.getMetrics).mockRejectedValue(new ApiError(500, "Server error"));

    render(<Metrics />);
    expect(await screen.findByText(/server error/i)).toBeDefined();
  });
});
