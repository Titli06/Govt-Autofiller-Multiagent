// Single reviewable field rendered with a confidence color (green/yellow/red),
// its value, source-document provenance, and approve/edit controls.
//
// Serves two callers with two different field shapes:
//   - Profile.tsx (Phase 1): a ProfileField, confirm/edit against `status`.
//   - Review.tsx (Phase 3): a FormFieldReviewOut, approve/edit/approve-blank against
//     `outstanding` (needs_review AND NOT reviewed), plus a "verified against source"
//     badge and an optional "also save to my profile" checkbox on correction.
// Phase 2's read-only draft view deliberately used a local DraftField instead of this
// component (see memory/phase2-decisions.md) — Phase 3 is where approve/correct
// controls are actually exercised.

import { useState } from "react";

import type { ConfidenceBand, FormFieldReviewOut, ProfileField } from "../types";

const BAND_LABEL: Record<ConfidenceBand, string> = {
  high: "green",
  medium: "yellow",
  low: "red",
};

// Friendlier copy for review reasons that need more explanation than an
// underscore-replaced field name gives a non-technical reviewer (Phase 4, §9).
// Anything not listed here falls back to the generic underscore-replace below.
const REVIEW_REASON_LABEL: Partial<Record<string, string>> = {
  inferred_mapping: "auto-matched field — please confirm",
};

type Field = ProfileField | FormFieldReviewOut;

function isReviewField(field: Field): field is FormFieldReviewOut {
  return "outstanding" in field;
}

interface Props {
  field: Field;
  // Profile mode (ProfileField)
  onConfirm?: (fieldId: string) => Promise<void>;
  // Review mode (FormFieldReviewOut)
  onApprove?: (fieldId: string) => Promise<void>;
  onApproveBlank?: (fieldId: string) => Promise<void>;
  // Shared — Review mode passes a third `propagate` argument when
  // showPropagateOption is set; Profile mode ignores it.
  onCorrect: (fieldId: string, value: string, propagate?: boolean) => Promise<void>;
  onViewSource: (documentId: string) => void;
  // Review mode only: show an "also save to my profile" checkbox on correction.
  showPropagateOption?: boolean;
}

export default function ConfidenceField({
  field,
  onConfirm,
  onApprove,
  onApproveBlank,
  onCorrect,
  onViewSource,
  showPropagateOption = false,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(field.display_value ?? "");
  const [propagate, setPropagate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const review = isReviewField(field);
  const needsAction = review
    ? field.outstanding
    : field.status === "needs_confirmation" || field.status === "failed_validation";
  const canApproveBlank = review && field.display_value === null;

  async function submitCorrection() {
    setBusy(true);
    setError(null);
    try {
      // Only pass the propagate flag in review mode — Profile's onCorrect(id, value)
      // takes exactly two arguments.
      if (showPropagateOption) {
        await onCorrect(field.id, draft, propagate);
      } else {
        await onCorrect(field.id, draft);
      }
      setEditing(false);
    } catch {
      setError("Couldn't save that value — check the format and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function runAction(action: (fieldId: string) => Promise<void>) {
    setBusy(true);
    try {
      await action(field.id);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="confidence-field"
      data-band={field.confidence_band}
      data-testid={`confidence-field-${field.id}`}
    >
      <div className="confidence-field-header">
        <span className="confidence-field-name">{field.field_name.replace(/_/g, " ")}</span>
        {field.high_stakes && <span className="badge">high-stakes</span>}
        {review && field.verified && <span className="badge badge-verified">verified against source</span>}
        <span className={`band-dot band-${BAND_LABEL[field.confidence_band]}`} aria-hidden="true" />
      </div>

      {editing ? (
        <div className="confidence-field-edit">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label={`Corrected value for ${field.field_name}`}
          />
          {showPropagateOption && (
            <label className="confidence-field-propagate">
              <input
                type="checkbox"
                checked={propagate}
                onChange={(e) => setPropagate(e.target.checked)}
              />
              Also save to my profile
            </label>
          )}
          <button type="button" disabled={busy} onClick={() => void submitCorrection()}>
            Save
          </button>
          <button type="button" className="link" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="confidence-field-value">{field.display_value ?? <em>Not filled</em>}</div>
      )}

      {error && <p className="error">{error}</p>}

      <div className="confidence-field-meta">
        <span>{Math.round(field.confidence * 100)}% confidence</span>
        {review ? (
          <span>
            {REVIEW_REASON_LABEL[field.review_reason ?? ""] ??
              (field.review_reason ?? "resolved").replace(/_/g, " ")}
          </span>
        ) : (
          <span>{field.status.replace(/_/g, " ")}</span>
        )}
        {field.source.document_id && (
          <button
            type="button"
            className="link"
            onClick={() => onViewSource(field.source.document_id as string)}
          >
            View source
          </button>
        )}
      </div>

      {!editing && needsAction && (
        <div className="confidence-field-actions">
          {!review && onConfirm && (
            <button type="button" disabled={busy} onClick={() => void runAction(onConfirm)}>
              Confirm
            </button>
          )}
          {review && onApprove && (
            <button type="button" disabled={busy} onClick={() => void runAction(onApprove)}>
              Approve
            </button>
          )}
          <button type="button" className="link" onClick={() => setEditing(true)}>
            Edit
          </button>
          {review && canApproveBlank && onApproveBlank && (
            <button type="button" className="link" disabled={busy} onClick={() => void runAction(onApproveBlank)}>
              Approve as blank
            </button>
          )}
        </div>
      )}
    </div>
  );
}
