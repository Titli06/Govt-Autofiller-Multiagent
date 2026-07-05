import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ConfidenceField from "./ConfidenceField";
import type { ProfileField } from "../types";

function makeField(overrides: Partial<ProfileField> = {}): ProfileField {
  return {
    id: "field-1",
    field_name: "full_name",
    display_value: "Rajesh Kumar",
    confidence: 0.85,
    confidence_band: "medium",
    high_stakes: false,
    status: "confirmed",
    source: { document_id: "doc-1", doc_type: "aadhaar" },
    ...overrides,
  };
}

describe("ConfidenceField", () => {
  it("renders the value and confidence band, with no actions when confirmed", () => {
    render(
      <ConfidenceField
        field={makeField()}
        onConfirm={vi.fn()}
        onCorrect={vi.fn()}
        onViewSource={vi.fn()}
      />,
    );

    expect(screen.getByText("Rajesh Kumar")).toBeDefined();
    expect(screen.getByText("85% confidence")).toBeDefined();
    expect(screen.queryByRole("button", { name: /confirm/i })).toBeNull();
  });

  it("shows Confirm/Edit for a field needing confirmation, and confirms on click", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <ConfidenceField
        field={makeField({ status: "needs_confirmation" })}
        onConfirm={onConfirm}
        onCorrect={vi.fn()}
        onViewSource={vi.fn()}
      />,
    );

    const confirmButton = screen.getByRole("button", { name: /^confirm$/i });
    fireEvent.click(confirmButton);

    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith("field-1"));
  });

  it("shows a high-stakes badge when the field is high-stakes", () => {
    render(
      <ConfidenceField
        field={makeField({ high_stakes: true, status: "needs_confirmation" })}
        onConfirm={vi.fn()}
        onCorrect={vi.fn()}
        onViewSource={vi.fn()}
      />,
    );
    expect(screen.getByText(/high-stakes/i)).toBeDefined();
  });

  it("editing a value calls onCorrect with the new value and exits edit mode", async () => {
    const onCorrect = vi.fn().mockResolvedValue(undefined);
    render(
      <ConfidenceField
        field={makeField({ status: "needs_confirmation" })}
        onConfirm={vi.fn()}
        onCorrect={onCorrect}
        onViewSource={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const input = screen.getByLabelText(/corrected value for full_name/i);
    fireEvent.change(input, { target: { value: "Suresh Kumar" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(onCorrect).toHaveBeenCalledWith("field-1", "Suresh Kumar"));
  });

  it("clicking View source calls onViewSource with the document id", () => {
    const onViewSource = vi.fn();
    render(
      <ConfidenceField
        field={makeField()}
        onConfirm={vi.fn()}
        onCorrect={vi.fn()}
        onViewSource={onViewSource}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /view source/i }));
    expect(onViewSource).toHaveBeenCalledWith("doc-1");
  });
});
