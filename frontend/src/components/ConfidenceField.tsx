// Single reviewable field rendered with a confidence color (green/yellow/red),
// its value, source-document provenance, and confirm/edit controls. Reused by the
// Phase 3 form-review UI, which is why band coloring lives here rather than inline.

import { useState } from "react";

import type { ConfidenceBand, ProfileField } from "../types";

const BAND_LABEL: Record<ConfidenceBand, string> = {
  high: "green",
  medium: "yellow",
  low: "red",
};

interface Props {
  field: ProfileField;
  onConfirm: (fieldId: string) => Promise<void>;
  onCorrect: (fieldId: string, value: string) => Promise<void>;
  onViewSource: (documentId: string) => void;
}

export default function ConfidenceField({ field, onConfirm, onCorrect, onViewSource }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(field.display_value);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsAction = field.status === "needs_confirmation" || field.status === "failed_validation";

  async function submitCorrection() {
    setBusy(true);
    setError(null);
    try {
      await onCorrect(field.id, draft);
      setEditing(false);
    } catch {
      setError("Couldn't save that value — check the format and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    setBusy(true);
    try {
      await onConfirm(field.id);
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
        <span className={`band-dot band-${BAND_LABEL[field.confidence_band]}`} aria-hidden="true" />
      </div>

      {editing ? (
        <div className="confidence-field-edit">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label={`Corrected value for ${field.field_name}`}
          />
          <button type="button" disabled={busy} onClick={() => void submitCorrection()}>
            Save
          </button>
          <button type="button" className="link" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="confidence-field-value">{field.display_value}</div>
      )}

      {error && <p className="error">{error}</p>}

      <div className="confidence-field-meta">
        <span>{Math.round(field.confidence * 100)}% confidence</span>
        <span>{field.status.replace(/_/g, " ")}</span>
        <button type="button" className="link" onClick={() => onViewSource(field.source.document_id)}>
          View source
        </button>
      </div>

      {!editing && needsAction && (
        <div className="confidence-field-actions">
          <button type="button" disabled={busy} onClick={() => void confirm()}>
            Confirm
          </button>
          <button type="button" className="link" onClick={() => setEditing(true)}>
            Edit
          </button>
        </div>
      )}
    </div>
  );
}
