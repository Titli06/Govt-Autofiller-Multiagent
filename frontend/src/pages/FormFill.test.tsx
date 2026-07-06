import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import FormFill from "./FormFill";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { ...actual.api, uploadForm: vi.fn(), getForm: vi.fn() } };
});

afterEach(() => vi.restoreAllMocks());

function selectFile(input: HTMLElement, name = "form.jpg", type = "image/jpeg") {
  const file = new File(["fake-bytes"], name, { type });
  fireEvent.change(input, { target: { files: [file] } });
}

const baseForm = {
  id: "form-1",
  form_type: "income_certificate" as const,
  display_name: "Income Certificate",
  detected_form_type: "income_certificate",
  fill_error: null,
  page_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  filled_at: "2026-01-01T00:00:05Z",
};

describe("FormFill page", () => {
  it("uploads the selected file with the chosen form type and polls to a filled draft", async () => {
    vi.mocked(api.uploadForm).mockResolvedValue({ form_id: "form-1", status: "pending" });
    vi.mocked(api.getForm).mockResolvedValue({
      ...baseForm,
      status: "filled",
      fields: [
        {
          id: "field-1",
          field_name: "applicant_name",
          profile_key: "full_name",
          display_value: "Ravi Kumar",
          confidence: 0.95,
          confidence_band: "high",
          high_stakes: false,
          transformed: false,
          needs_review: false,
          review_reason: null,
          reviewed: false,
          source: { profile_field_id: "pf-1", document_id: "doc-1", doc_type: "aadhaar" },
        },
        {
          id: "field-2",
          field_name: "annual_income",
          profile_key: null,
          display_value: null,
          confidence: 0,
          confidence_band: "low",
          high_stakes: true,
          transformed: false,
          needs_review: true,
          review_reason: "no_mapping",
          reviewed: false,
          source: { profile_field_id: null, document_id: null, doc_type: null },
        },
      ],
    });

    render(
      <MemoryRouter>
        <FormFill />
      </MemoryRouter>,
    );

    selectFile(screen.getByLabelText(/blank form image or pdf/i));
    fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() =>
      expect(api.uploadForm).toHaveBeenCalledWith(expect.any(File), "income_certificate"),
    );
    await waitFor(() => expect(screen.getByText(/status: filled/i)).toBeDefined());

    expect(screen.getByText("Ravi Kumar")).toBeDefined();
    expect(screen.getByTestId("draft-field-annual_income")).toBeDefined();
    expect(screen.getByText(/no mapping/i)).toBeDefined();
    // Phase 2's draft is read-only: no approve/edit/download controls.
    expect(screen.queryByRole("button", { name: /confirm/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /download/i })).toBeNull();
  });

  it("shows a clear message when the form type doesn't match", async () => {
    vi.mocked(api.uploadForm).mockResolvedValue({ form_id: "form-1", status: "pending" });
    vi.mocked(api.getForm).mockResolvedValue({
      ...baseForm,
      detected_form_type: "scholarship_application",
      status: "type_mismatch",
      fill_error: "declared=income_certificate detected=scholarship_application",
      fields: [],
    });

    render(
      <MemoryRouter>
        <FormFill />
      </MemoryRouter>,
    );

    selectFile(screen.getByLabelText(/blank form image or pdf/i));
    fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText(/not a income certificate/i)).toBeDefined());
  });

  it("disables the upload button until a file is chosen", () => {
    render(
      <MemoryRouter>
        <FormFill />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: /^upload$/i })).toHaveProperty("disabled", true);
  });
});
