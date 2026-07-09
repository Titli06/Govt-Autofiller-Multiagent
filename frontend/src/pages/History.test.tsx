import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { HistoryItem } from "../types";
import History from "./History";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      getHistory: vi.fn(),
      downloadForm: vi.fn(),
      deleteMyData: vi.fn(),
    },
  };
});

afterEach(() => vi.restoreAllMocks());

function historyItem(overrides: Partial<HistoryItem> = {}): HistoryItem {
  return {
    id: "form-1",
    form_type: "income_certificate",
    display_name: "Income Certificate",
    schema_source: "template",
    status: "approved",
    fill_error: null,
    total_fields: 3,
    outstanding_fields: 0,
    download_ready: true,
    created_at: "2026-07-01T10:00:00Z",
    filled_at: "2026-07-01T10:00:05Z",
    ...overrides,
  };
}

function renderHistory() {
  return render(
    <MemoryRouter>
      <History />
    </MemoryRouter>,
  );
}

describe("History page", () => {
  it("shows an empty-state message when there are no forms", async () => {
    vi.mocked(api.getHistory).mockResolvedValue({ forms: [] });
    renderHistory();
    expect(await screen.findByText(/no forms yet/i)).toBeDefined();
  });

  it("renders a form and its schema_source badge for an inferred form", async () => {
    vi.mocked(api.getHistory).mockResolvedValue({
      forms: [
        historyItem({
          id: "form-2",
          display_name: "Marriage Certificate",
          schema_source: "inferred",
          status: "in_review",
          total_fields: 4,
          outstanding_fields: 2,
        }),
      ],
    });

    renderHistory();

    expect(await screen.findByText("Marriage Certificate")).toBeDefined();
    expect(screen.getByTestId("schema-source-badge").textContent).toBe("Auto-detected");
    expect(screen.getByText(/2 of 4 fields resolved/i)).toBeDefined();
  });

  it("does not show the schema_source badge for a template form", async () => {
    vi.mocked(api.getHistory).mockResolvedValue({ forms: [historyItem()] });
    renderHistory();
    await screen.findByText("Income Certificate");
    expect(screen.queryByTestId("schema-source-badge")).toBeNull();
  });

  it("shows a Download action for an approved form", async () => {
    vi.mocked(api.getHistory).mockResolvedValue({ forms: [historyItem({ status: "approved" })] });
    const blob = new Blob(["pdf-bytes"], { type: "application/pdf" });
    vi.mocked(api.downloadForm).mockResolvedValue(blob);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn().mockReturnValue("blob:fake-url"),
      revokeObjectURL: vi.fn(),
    });

    renderHistory();
    await screen.findByText("Income Certificate");

    fireEvent.click(screen.getByRole("button", { name: /^download$/i }));
    await waitFor(() => expect(api.downloadForm).toHaveBeenCalledWith("form-1"));
  });

  it("shows a Continue review link for an in_review form, not a download button", async () => {
    vi.mocked(api.getHistory).mockResolvedValue({
      forms: [historyItem({ status: "in_review", outstanding_fields: 1 })],
    });

    renderHistory();
    await screen.findByText("Income Certificate");

    expect(screen.getByRole("link", { name: /continue review/i })).toHaveProperty(
      "href",
      expect.stringContaining("/forms/form-1/review"),
    );
    expect(screen.queryByRole("button", { name: /^download$/i })).toBeNull();
  });

  it("shows the failure reason for a failed form, with no action", async () => {
    vi.mocked(api.getHistory).mockResolvedValue({
      forms: [historyItem({ status: "failed", fill_error: "could not detect any fields" })],
    });

    renderHistory();

    expect(await screen.findByText("could not detect any fields")).toBeDefined();
    expect(screen.queryByRole("button", { name: /^download$/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /continue review/i })).toBeNull();
  });

  // --- Delete-my-data flow (Decision 1/3) ----------------------------------------------

  it("requires opening the confirm modal before a password field appears", async () => {
    vi.mocked(api.getHistory).mockResolvedValue({ forms: [] });
    renderHistory();
    await screen.findByText(/no forms yet/i);

    expect(screen.queryByTestId("delete-modal")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /delete all my data/i }));
    expect(screen.getByTestId("delete-modal")).toBeDefined();
    expect(screen.getByLabelText(/password/i)).toBeDefined();
  });

  it("disables the destructive confirm button until a password is entered", async () => {
    vi.mocked(api.getHistory).mockResolvedValue({ forms: [] });
    renderHistory();
    await screen.findByText(/no forms yet/i);

    fireEvent.click(screen.getByRole("button", { name: /delete all my data/i }));
    const confirmButton = screen.getByRole("button", { name: /permanently delete everything/i });
    expect(confirmButton).toHaveProperty("disabled", true);

    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "supersecret1" } });
    expect(confirmButton).toHaveProperty("disabled", false);
  });

  it("on success, shows the returned counts and clears the form list", async () => {
    vi.mocked(api.getHistory).mockResolvedValue({ forms: [historyItem()] });
    vi.mocked(api.deleteMyData).mockResolvedValue({
      documents_deleted: 1,
      forms_deleted: 1,
      profile_fields_deleted: 2,
      s3_objects_deleted: 3,
      s3_delete_failures: 0,
    });

    renderHistory();
    await screen.findByText("Income Certificate");

    fireEvent.click(screen.getByRole("button", { name: /delete all my data/i }));
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "supersecret1" } });
    fireEvent.click(screen.getByRole("button", { name: /permanently delete everything/i }));

    await waitFor(() => expect(api.deleteMyData).toHaveBeenCalledWith("supersecret1"));
    const successMsg = await screen.findByTestId("delete-success");
    expect(successMsg.textContent).toContain("1 form(s), 1 document(s), and 2 profile field(s)");
    expect(screen.queryByText("Income Certificate")).toBeNull();
    expect(screen.getByText(/no forms yet/i)).toBeDefined();
  });

  it("on a wrong password, shows an error and keeps the modal open", async () => {
    const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
    vi.mocked(api.getHistory).mockResolvedValue({ forms: [] });
    vi.mocked(api.deleteMyData).mockRejectedValue(
      new ApiError(403, "Password is incorrect", "INVALID_PASSWORD"),
    );

    renderHistory();
    await screen.findByText(/no forms yet/i);

    fireEvent.click(screen.getByRole("button", { name: /delete all my data/i }));
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /permanently delete everything/i }));

    await waitFor(() => expect(screen.getByText(/password is incorrect/i)).toBeDefined());
    expect(screen.getByTestId("delete-modal")).toBeDefined();
  });

  it("on jobs-in-progress, shows a retry message", async () => {
    const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
    vi.mocked(api.getHistory).mockResolvedValue({ forms: [] });
    vi.mocked(api.deleteMyData).mockRejectedValue(
      new ApiError(409, "A document or form is still being processed", "JOBS_IN_PROGRESS"),
    );

    renderHistory();
    await screen.findByText(/no forms yet/i);

    fireEvent.click(screen.getByRole("button", { name: /delete all my data/i }));
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "supersecret1" } });
    fireEvent.click(screen.getByRole("button", { name: /permanently delete everything/i }));

    await waitFor(() => expect(screen.getByText(/try again in a moment/i)).toBeDefined());
  });

  it("shows an error message when history fails to load", async () => {
    const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
    vi.mocked(api.getHistory).mockRejectedValue(new ApiError(500, "Server error"));

    renderHistory();

    await waitFor(() => expect(screen.getByText(/server error/i)).toBeDefined());
  });
});
