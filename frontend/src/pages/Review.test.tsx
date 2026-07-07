import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { FormFieldReviewOut, FormReviewOut } from "../types";
import Review from "./Review";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      getFormReview: vi.fn(),
      submitReview: vi.fn(),
      getDocumentFile: vi.fn(),
      getFormFile: vi.fn(),
      downloadForm: vi.fn(),
    },
  };
});

afterEach(() => vi.restoreAllMocks());

function reviewField(overrides: Partial<FormFieldReviewOut> = {}): FormFieldReviewOut {
  return {
    id: "field-1",
    field_name: "applicant_name",
    profile_key: "full_name",
    display_value: "Ravi Kumar",
    confidence: 0.95,
    confidence_band: "high",
    verified: true,
    verification_method: "exact",
    high_stakes: false,
    transformed: false,
    needs_review: false,
    review_reason: null,
    reviewed: false,
    review_action: null,
    outstanding: false,
    source: { profile_field_id: "pf-1", document_id: "doc-1", doc_type: "aadhaar" },
    ...overrides,
  };
}

function reviewOut(overrides: Partial<FormReviewOut> = {}): FormReviewOut {
  return {
    id: "form-1",
    form_type: "income_certificate",
    display_name: "Income Certificate",
    status: "in_review",
    download_ready: false,
    total_fields: 1,
    outstanding_fields: 0,
    placement_warning: null,
    fields: [reviewField()],
    ...overrides,
  };
}

function renderReview() {
  return render(
    <MemoryRouter initialEntries={["/forms/form-1/review"]}>
      <Routes>
        <Route path="/forms/:id/review" element={<Review />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Review page", () => {
  it("loads and renders fields, with download disabled while review is incomplete", async () => {
    vi.mocked(api.getFormReview).mockResolvedValue(
      reviewOut({
        outstanding_fields: 1,
        fields: [reviewField({ id: "f2", outstanding: true, review_reason: "high_stakes", high_stakes: true })],
      }),
    );

    renderReview();

    await waitFor(() => expect(api.getFormReview).toHaveBeenCalledWith("form-1"));
    expect(await screen.findByText("Ravi Kumar")).toBeDefined();
    expect(screen.getByRole("button", { name: /download filled form/i })).toHaveProperty("disabled", true);
  });

  it("shows the placement warning banner and can dismiss it", async () => {
    vi.mocked(api.getFormReview).mockResolvedValue(
      reviewOut({ placement_warning: "This scan looks rotated ~12°; re-scan upright." }),
    );

    renderReview();

    expect(await screen.findByTestId("placement-warning")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    await waitFor(() => expect(screen.queryByTestId("placement-warning")).toBeNull());
  });

  it("approving the last outstanding field enables download", async () => {
    const outstandingField = reviewField({
      id: "f2",
      outstanding: true,
      needs_review: true,
      review_reason: "high_stakes",
      high_stakes: true,
    });
    vi.mocked(api.getFormReview).mockResolvedValue(
      reviewOut({ outstanding_fields: 1, fields: [outstandingField] }),
    );
    vi.mocked(api.submitReview).mockResolvedValue({
      field: { ...outstandingField, outstanding: false, reviewed: true, review_action: "approved" },
      status: "approved",
      download_ready: true,
      warning: null,
    });

    renderReview();
    await screen.findByText("Ravi Kumar");

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));

    await waitFor(() =>
      expect(api.submitReview).toHaveBeenCalledWith("form-1", { field_id: "f2", action: "approve" }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /download filled form/i })).toHaveProperty("disabled", false),
    );
  });

  it("clicking Download fetches the PDF blob", async () => {
    vi.mocked(api.getFormReview).mockResolvedValue(reviewOut({ download_ready: true }));
    const blob = new Blob(["pdf-bytes"], { type: "application/pdf" });
    vi.mocked(api.downloadForm).mockResolvedValue(blob);
    const createObjectURL = vi.fn().mockReturnValue("blob:fake-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    renderReview();
    await screen.findByText("Ravi Kumar");

    fireEvent.click(screen.getByRole("button", { name: /download filled form/i }));

    await waitFor(() => expect(api.downloadForm).toHaveBeenCalledWith("form-1"));
  });

  it("shows an error message when the review fails to load", async () => {
    const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
    vi.mocked(api.getFormReview).mockRejectedValue(new ApiError(404, "Form not found"));

    renderReview();

    await waitFor(() => expect(screen.getByText(/form not found/i)).toBeDefined());
  });
});
