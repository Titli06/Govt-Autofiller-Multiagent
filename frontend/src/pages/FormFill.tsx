// Form Fill page: upload a blank form the system has a template for (UC2). Kicks off
// the async LangGraph fill job and polls its status until terminal, then renders a
// READ-ONLY draft — every field with its provisional confidence band and review flag.
// No approve/edit/download here; that's Phase 3's review UI.

import { FormEvent, useCallback, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { ConfidenceBand, FormFieldOut, FormOut, FormType } from "../types";

const TERMINAL_STATUSES = new Set<FormOut["status"]>(["filled", "failed", "type_mismatch"]);
const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 30; // ~1 minute before we stop polling and let the user retry

const FORM_TYPE_LABELS: Record<FormType, string> = {
  income_certificate: "Income Certificate",
  scholarship_application: "Scholarship Application",
};

const BAND_LABEL: Record<ConfidenceBand, string> = {
  high: "green",
  medium: "yellow",
  low: "red",
};

function DraftField({ field }: { field: FormFieldOut }) {
  return (
    <div className="confidence-field" data-band={field.confidence_band} data-testid={`draft-field-${field.field_name}`}>
      <div className="confidence-field-header">
        <span className="confidence-field-name">{field.field_name.replace(/_/g, " ")}</span>
        {field.high_stakes && <span className="badge">high-stakes</span>}
        {field.needs_review && <span className="badge">needs review</span>}
        <span className={`band-dot band-${BAND_LABEL[field.confidence_band]}`} aria-hidden="true" />
      </div>
      <div className="confidence-field-value">{field.display_value ?? <em>Not filled</em>}</div>
      <div className="confidence-field-meta">
        <span>{Math.round(field.confidence * 100)}% confidence</span>
        {field.transformed && <span>reformatted</span>}
        {field.review_reason && <span>{field.review_reason.replace(/_/g, " ")}</span>}
      </div>
    </div>
  );
}

export default function FormFill() {
  const [formType, setFormType] = useState<FormType>("income_certificate");
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState<FormOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollCount = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async (formId: string) => {
    try {
      const result = await api.getForm(formId);
      setForm(result);
      pollCount.current += 1;
      if (!TERMINAL_STATUSES.has(result.status) && pollCount.current < MAX_POLLS) {
        pollTimer.current = setTimeout(() => void poll(formId), POLL_INTERVAL_MS);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't check form status");
    }
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    if (pollTimer.current) clearTimeout(pollTimer.current);
    setError(null);
    setForm(null);
    setSubmitting(true);
    pollCount.current = 0;
    try {
      const upload = await api.uploadForm(file, formType);
      void poll(upload.form_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h1>Fill a form</h1>
      <form onSubmit={(e) => void onSubmit(e)}>
        <div className="field">
          <label htmlFor="form-type">Form type</label>
          <select
            id="form-type"
            value={formType}
            onChange={(e) => setFormType(e.target.value as FormType)}
          >
            {Object.entries(FORM_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="form-file">Blank form image or PDF</label>
          <input
            id="form-file"
            type="file"
            accept="image/jpeg,image/png,image/webp,image/heic,image/heif,application/pdf"
            capture="environment"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={!file || submitting}>
          {submitting ? "Uploading…" : "Upload"}
        </button>
      </form>

      {form && (
        <div className="upload-status">
          <p>Status: {form.status.replace(/_/g, " ")}</p>

          {form.status === "type_mismatch" && (
            <p className="error">
              This looks like a {form.detected_form_type ?? "different"} form, not a{" "}
              {FORM_TYPE_LABELS[form.form_type]}. Please re-select the correct type and re-upload.
            </p>
          )}
          {form.status === "failed" && <p className="error">{form.fill_error ?? "Fill failed."}</p>}

          {form.status === "filled" && (
            <>
              <p className="notice">
                Draft ready — this is not final and has not been submitted anywhere. Review
                arrives in a later step.
              </p>
              {form.fields.map((field) => (
                <DraftField key={field.id} field={field} />
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
