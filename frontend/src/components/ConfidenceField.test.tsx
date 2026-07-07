import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ConfidenceField from "./ConfidenceField";
import type { FormFieldReviewOut, ProfileField } from "../types";

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

function makeReviewField(overrides: Partial<FormFieldReviewOut> = {}): FormFieldReviewOut {
  return {
    id: "field-1",
    field_name: "aadhaar_number",
    profile_key: "aadhaar_number",
    display_value: "XXXX XXXX 2346",
    confidence: 0.95,
    confidence_band: "high",
    verified: true,
    verification_method: "exact",
    high_stakes: true,
    transformed: false,
    needs_review: true,
    review_reason: "high_stakes",
    reviewed: false,
    review_action: null,
    outstanding: true,
    source: { profile_field_id: "pf-1", document_id: "doc-1", doc_type: "aadhaar" },
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

describe("ConfidenceField in review mode (FormFieldReviewOut)", () => {
  it("shows a verified badge and the review reason, with Approve/Edit for an outstanding field", () => {
    render(
      <ConfidenceField
        field={makeReviewField()}
        onApprove={vi.fn()}
        onCorrect={vi.fn()}
        onViewSource={vi.fn()}
      />,
    );

    expect(screen.getByText(/verified against source/i)).toBeDefined();
    expect(screen.getByText(/high stakes/i)).toBeDefined();
    expect(screen.getByRole("button", { name: /^approve$/i })).toBeDefined();
    expect(screen.getByRole("button", { name: /edit/i })).toBeDefined();
  });

  it("a resolved (not outstanding) field shows no action buttons", () => {
    render(
      <ConfidenceField
        field={makeReviewField({ outstanding: false, reviewed: true, review_action: "approved" })}
        onApprove={vi.fn()}
        onCorrect={vi.fn()}
        onViewSource={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /^approve$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull();
  });

  it("clicking Approve calls onApprove with the field id", async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(
      <ConfidenceField
        field={makeReviewField()}
        onApprove={onApprove}
        onCorrect={vi.fn()}
        onViewSource={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith("field-1"));
  });

  it("shows Approve as blank only for a field with no value, and calls onApproveBlank", async () => {
    const onApproveBlank = vi.fn().mockResolvedValue(undefined);
    render(
      <ConfidenceField
        field={makeReviewField({ display_value: null, review_reason: "no_mapping" })}
        onApprove={vi.fn()}
        onApproveBlank={onApproveBlank}
        onCorrect={vi.fn()}
        onViewSource={vi.fn()}
      />,
    );

    const blankButton = screen.getByRole("button", { name: /approve as blank/i });
    fireEvent.click(blankButton);
    await waitFor(() => expect(onApproveBlank).toHaveBeenCalledWith("field-1"));
  });

  it("does not show Approve as blank for a field that already has a value", () => {
    render(
      <ConfidenceField
        field={makeReviewField()}
        onApprove={vi.fn()}
        onApproveBlank={vi.fn()}
        onCorrect={vi.fn()}
        onViewSource={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /approve as blank/i })).toBeNull();
  });

  it("editing with showPropagateOption sends the propagate flag to onCorrect", async () => {
    const onCorrect = vi.fn().mockResolvedValue(undefined);
    render(
      <ConfidenceField
        field={makeReviewField()}
        onApprove={vi.fn()}
        onCorrect={onCorrect}
        onViewSource={vi.fn()}
        showPropagateOption
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const input = screen.getByLabelText(/corrected value for aadhaar_number/i);
    fireEvent.change(input, { target: { value: "234123412346" } });
    fireEvent.click(screen.getByLabelText(/also save to my profile/i));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(onCorrect).toHaveBeenCalledWith("field-1", "234123412346", true),
    );
  });
});
