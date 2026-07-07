import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
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
  schema_source: "template" as const,
  fill_error: null,
  page_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  filled_at: "2026-01-01T00:00:05Z",
};

function ReviewStub() {
  const { id } = useParams<{ id: string }>();
  return <div>Review page for form {id}</div>;
}

function renderWithRouting() {
  return render(
    <MemoryRouter initialEntries={["/forms"]}>
      <Routes>
        <Route path="/forms" element={<FormFill />} />
        <Route path="/forms/:id/review" element={<ReviewStub />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("FormFill page", () => {
  it("uploads the selected file and navigates to the review page once in_review", async () => {
    vi.mocked(api.uploadForm).mockResolvedValue({ form_id: "form-1", status: "pending" });
    vi.mocked(api.getForm).mockResolvedValue({ ...baseForm, status: "in_review", fields: [] });

    renderWithRouting();

    selectFile(screen.getByLabelText(/blank form image or pdf/i));
    fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() =>
      expect(api.uploadForm).toHaveBeenCalledWith(expect.any(File), "income_certificate"),
    );
    await waitFor(() => expect(screen.getByText(/review page for form form-1/i)).toBeDefined());
  });

  it("navigates to review once approved (zero-flag pipeline)", async () => {
    vi.mocked(api.uploadForm).mockResolvedValue({ form_id: "form-1", status: "pending" });
    vi.mocked(api.getForm).mockResolvedValue({ ...baseForm, status: "approved", fields: [] });

    renderWithRouting();

    selectFile(screen.getByLabelText(/blank form image or pdf/i));
    fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText(/review page for form form-1/i)).toBeDefined());
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

    renderWithRouting();

    selectFile(screen.getByLabelText(/blank form image or pdf/i));
    fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText(/not a income certificate/i)).toBeDefined());
  });

  it("shows the fill error when the job fails", async () => {
    vi.mocked(api.uploadForm).mockResolvedValue({ form_id: "form-1", status: "pending" });
    vi.mocked(api.getForm).mockResolvedValue({
      ...baseForm,
      status: "failed",
      fill_error: "unsupported form type",
      fields: [],
    });

    renderWithRouting();

    selectFile(screen.getByLabelText(/blank form image or pdf/i));
    fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText("unsupported form type")).toBeDefined());
  });

  it("disables the upload button until a file is chosen", () => {
    renderWithRouting();
    expect(screen.getByRole("button", { name: /^upload$/i })).toHaveProperty("disabled", true);
  });

  // --- Phase 4: "Other / not listed" free-text form type (SPEC-PHASE4.md §9) -------

  it("reveals a free-text input when Other is selected and submits its value as form_type", async () => {
    vi.mocked(api.uploadForm).mockResolvedValue({ form_id: "form-2", status: "pending" });
    vi.mocked(api.getForm).mockResolvedValue({
      ...baseForm,
      id: "form-2",
      schema_source: "inferred",
      status: "in_review",
      fields: [],
    });

    renderWithRouting();

    fireEvent.change(screen.getByLabelText(/form type/i), { target: { value: "__other__" } });
    const customInput = screen.getByLabelText(/what form is this/i);
    fireEvent.change(customInput, { target: { value: "Marriage Certificate" } });
    selectFile(screen.getByLabelText(/blank form image or pdf/i));
    fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() =>
      expect(api.uploadForm).toHaveBeenCalledWith(expect.any(File), "Marriage Certificate"),
    );
  });

  it("disables upload when Other is selected but no custom form name is typed", () => {
    renderWithRouting();
    fireEvent.change(screen.getByLabelText(/form type/i), { target: { value: "__other__" } });
    selectFile(screen.getByLabelText(/blank form image or pdf/i));
    expect(screen.getByRole("button", { name: /^upload$/i })).toHaveProperty("disabled", true);
  });
});
