// Doc Upload page: drag-drop + camera capture for ID documents (UC1). Kicks off the
// async OCR extraction job and polls its status until it reaches a terminal state.

import { FormEvent, useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { DocType, DocumentStatus } from "../types";

const TERMINAL_STATUSES = new Set<DocumentStatus["ocr_status"]>([
  "extracted",
  "partial",
  "failed",
  "type_mismatch",
]);
const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 30; // ~1 minute before we stop polling and let the user retry

export default function Upload() {
  const [docType, setDocType] = useState<DocType>("aadhaar");
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollCount = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async (documentId: string) => {
    try {
      const result = await api.getDocumentStatus(documentId);
      setStatus(result);
      pollCount.current += 1;
      if (!TERMINAL_STATUSES.has(result.ocr_status) && pollCount.current < MAX_POLLS) {
        pollTimer.current = setTimeout(() => void poll(documentId), POLL_INTERVAL_MS);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't check upload status");
    }
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    if (pollTimer.current) clearTimeout(pollTimer.current);
    setError(null);
    setStatus(null);
    setSubmitting(true);
    pollCount.current = 0;
    try {
      const upload = await api.uploadDocument(file, docType);
      setStatus({
        id: upload.document_id,
        declared_doc_type: docType,
        detected_doc_type: null,
        ocr_status: upload.ocr_status,
        ocr_error: null,
        page_count: null,
        created_at: new Date().toISOString(),
        extracted_at: null,
      });
      void poll(upload.document_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h1>Upload an ID document</h1>
      <form onSubmit={(e) => void onSubmit(e)}>
        <div className="field">
          <label htmlFor="doc-type">Document type</label>
          <select
            id="doc-type"
            value={docType}
            onChange={(e) => setDocType(e.target.value as DocType)}
          >
            <option value="aadhaar">Aadhaar</option>
            <option value="pan">PAN</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="file">Document image or PDF</label>
          <input
            id="file"
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

      {status && (
        <div className="upload-status">
          <p>Status: {status.ocr_status.replace(/_/g, " ")}</p>

          {status.ocr_status === "type_mismatch" && (
            <p className="error">
              This looks like a {status.detected_doc_type ?? "different"} document, not a{" "}
              {status.declared_doc_type}. Please re-select the correct type and re-upload.
            </p>
          )}
          {status.ocr_status === "failed" && (
            <p className="error">{status.ocr_error ?? "Extraction failed."}</p>
          )}
          {(status.ocr_status === "extracted" || status.ocr_status === "partial") && (
            <p className="notice">
              Done — <Link to="/profile">view your profile</Link>.
              {status.ocr_status === "partial" && " Some fields need your confirmation."}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
