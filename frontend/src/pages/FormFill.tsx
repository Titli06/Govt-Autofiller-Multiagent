// Form Fill page: upload a blank form the system has a template for (UC2), or one it
// doesn't (UC3/Phase 4 — schema inference) via the "Other / not listed" option. Kicks
// off the async LangGraph fill job and polls its status until terminal. On success
// (in_review or approved — "filled" is retired as of Phase 3) it routes straight to
// the Review page, which is where fields, confidence, and download actually live.
// This page only handles upload + polling + the type_mismatch/failed error states.

import { FormEvent, useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { FormOut, FormType } from "../types";

const SUCCESS_STATUSES = new Set<FormOut["status"]>(["in_review", "approved"]);
const TERMINAL_STATUSES = new Set<FormOut["status"]>(["in_review", "approved", "failed", "type_mismatch"]);
const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 30; // ~1 minute before we stop polling and let the user retry

const FORM_TYPE_LABELS: Record<FormType, string> = {
  income_certificate: "Income Certificate",
  scholarship_application: "Scholarship Application",
};

// Sentinel for the "Other / not listed" option (Phase 4, SPEC-PHASE4.md §9) — never
// sent to the API itself; the free-text input's value is sent as form_type instead.
const OTHER_FORM_TYPE = "__other__";

export default function FormFill() {
  const navigate = useNavigate();
  const [formType, setFormType] = useState<string>("income_certificate");
  const [customFormType, setCustomFormType] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState<FormOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollCount = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(
    async (formId: string) => {
      try {
        const result = await api.getForm(formId);
        if (SUCCESS_STATUSES.has(result.status)) {
          navigate(`/forms/${formId}/review`);
          return;
        }
        setForm(result);
        pollCount.current += 1;
        if (!TERMINAL_STATUSES.has(result.status) && pollCount.current < MAX_POLLS) {
          pollTimer.current = setTimeout(() => void poll(formId), POLL_INTERVAL_MS);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Couldn't check form status");
      }
    },
    [navigate],
  );

  const isOther = formType === OTHER_FORM_TYPE;
  const effectiveFormType = isOther ? customFormType.trim() : formType;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file || !effectiveFormType) return;
    if (pollTimer.current) clearTimeout(pollTimer.current);
    setError(null);
    setForm(null);
    setSubmitting(true);
    pollCount.current = 0;
    try {
      const upload = await api.uploadForm(file, effectiveFormType);
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
          <select id="form-type" value={formType} onChange={(e) => setFormType(e.target.value)}>
            {Object.entries(FORM_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
            <option value={OTHER_FORM_TYPE}>Other / not listed</option>
          </select>
        </div>
        {isOther && (
          <div className="field">
            <label htmlFor="custom-form-type">What form is this?</label>
            <input
              id="custom-form-type"
              value={customFormType}
              onChange={(e) => setCustomFormType(e.target.value)}
              placeholder="e.g. Marriage Certificate"
              maxLength={64}
            />
            <p className="notice">
              We haven't seen this form before — we'll detect its fields automatically and
              you'll review every one before it's ready to download.
            </p>
          </div>
        )}
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
        <button type="submit" disabled={!file || !effectiveFormType || submitting}>
          {submitting ? "Uploading…" : "Upload"}
        </button>
      </form>

      {form && (
        <div className="upload-status">
          <p>Status: {form.status.replace(/_/g, " ")}</p>

          {form.status === "type_mismatch" && (
            <p className="error">
              This looks like a {form.detected_form_type ?? "different"} form, not a{" "}
              {/* form_type is a plain string now (Phase 5 widening) — only a known
                  registry type ever reaches type_mismatch (SPEC-PHASE4.md Decision 2),
                  but guard the lookup anyway rather than assume it. */}
              {FORM_TYPE_LABELS[form.form_type as FormType] ?? form.form_type}. Please re-select
              the correct type and re-upload.
            </p>
          )}
          {form.status === "failed" && <p className="error">{form.fill_error ?? "Fill failed."}</p>}
        </div>
      )}
    </div>
  );
}
