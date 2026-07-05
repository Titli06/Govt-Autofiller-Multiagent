import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { ProfileField } from "../types";
import Profile from "./Profile";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      getProfile: vi.fn(),
      confirmField: vi.fn(),
      correctField: vi.fn(),
      getDocumentFile: vi.fn(),
    },
  };
});

afterEach(() => vi.restoreAllMocks());

function field(overrides: Partial<ProfileField> = {}): ProfileField {
  return {
    id: "field-1",
    field_name: "full_name",
    display_value: "Rajesh Kumar",
    confidence: 0.9,
    confidence_band: "high",
    high_stakes: false,
    status: "confirmed",
    source: { document_id: "doc-1", doc_type: "aadhaar" },
    ...overrides,
  };
}

describe("Profile page", () => {
  it("shows an empty-state message when there are no fields yet", async () => {
    vi.mocked(api.getProfile).mockResolvedValue({ fields: [] });
    render(<Profile />);
    await waitFor(() => expect(screen.getByText(/upload an id document/i)).toBeDefined());
  });

  it("renders extracted fields grouped, and confirms a flagged field", async () => {
    vi.mocked(api.getProfile).mockResolvedValue({
      fields: [
        field({ id: "f1", field_name: "full_name", status: "needs_confirmation" }),
        field({
          id: "f2",
          field_name: "aadhaar_number",
          display_value: "XXXX XXXX 2346",
          high_stakes: true,
          status: "needs_confirmation",
        }),
      ],
    });
    vi.mocked(api.confirmField).mockResolvedValue(
      field({ id: "f1", field_name: "full_name", status: "user_confirmed" }),
    );

    render(<Profile />);

    await waitFor(() => expect(screen.getByText("Rajesh Kumar")).toBeDefined());
    expect(screen.getByText("XXXX XXXX 2346")).toBeDefined();

    const confirmButtons = screen.getAllByRole("button", { name: /^confirm$/i });
    fireEvent.click(confirmButtons[0]);

    await waitFor(() => expect(api.confirmField).toHaveBeenCalledWith("f1"));
  });

  it("loads and displays the source document preview on 'View source'", async () => {
    vi.mocked(api.getProfile).mockResolvedValue({ fields: [field()] });
    const blob = new Blob(["fake-image-bytes"], { type: "image/jpeg" });
    vi.mocked(api.getDocumentFile).mockResolvedValue(blob);
    const createObjectURL = vi.fn().mockReturnValue("blob:fake-url");
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL: vi.fn() });

    render(<Profile />);
    await waitFor(() => expect(screen.getByText("Rajesh Kumar")).toBeDefined());

    fireEvent.click(screen.getByRole("button", { name: /view source/i }));

    await waitFor(() => expect(api.getDocumentFile).toHaveBeenCalledWith("doc-1"));
    await waitFor(() => expect(screen.getByAltText(/source document/i)).toBeDefined());
  });

  it("shows an error message when the profile fails to load", async () => {
    const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
    vi.mocked(api.getProfile).mockRejectedValue(new ApiError(500, "Server error"));

    render(<Profile />);

    await waitFor(() => expect(screen.getByText(/server error/i)).toBeDefined());
  });
});
