import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import Upload from "./Upload";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { ...actual.api, uploadDocument: vi.fn(), getDocumentStatus: vi.fn() } };
});

afterEach(() => vi.restoreAllMocks());

function selectFile(input: HTMLElement, name = "aadhaar.jpg", type = "image/jpeg") {
  const file = new File(["fake-bytes"], name, { type });
  fireEvent.change(input, { target: { files: [file] } });
}

describe("Upload page", () => {
  it("uploads the selected file with the chosen doc type and polls status to a terminal state", async () => {
    vi.mocked(api.uploadDocument).mockResolvedValue({ document_id: "doc-1", ocr_status: "pending" });
    vi.mocked(api.getDocumentStatus).mockResolvedValue({
      id: "doc-1",
      declared_doc_type: "aadhaar",
      detected_doc_type: "aadhaar",
      ocr_status: "extracted",
      ocr_error: null,
      page_count: 1,
      created_at: "2026-01-01T00:00:00Z",
      extracted_at: "2026-01-01T00:00:05Z",
    });

    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>,
    );

    selectFile(screen.getByLabelText(/document image or pdf/i));
    fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(api.uploadDocument).toHaveBeenCalledWith(expect.any(File), "aadhaar"));
    await waitFor(() => expect(screen.getByText(/status: extracted/i)).toBeDefined());
    expect(screen.getByText(/view your profile/i)).toBeDefined();
  });

  it("shows a clear message when the document type doesn't match", async () => {
    vi.mocked(api.uploadDocument).mockResolvedValue({ document_id: "doc-1", ocr_status: "pending" });
    vi.mocked(api.getDocumentStatus).mockResolvedValue({
      id: "doc-1",
      declared_doc_type: "aadhaar",
      detected_doc_type: "pan",
      ocr_status: "type_mismatch",
      ocr_error: "declared=aadhaar detected=pan",
      page_count: 1,
      created_at: "2026-01-01T00:00:00Z",
      extracted_at: "2026-01-01T00:00:05Z",
    });

    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>,
    );

    selectFile(screen.getByLabelText(/document image or pdf/i));
    fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText(/not a aadhaar/i)).toBeDefined());
  });

  it("disables the upload button until a file is chosen", () => {
    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: /^upload$/i })).toHaveProperty("disabled", true);
  });
});
