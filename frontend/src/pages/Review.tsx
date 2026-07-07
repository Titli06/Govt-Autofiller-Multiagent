// Review/Edit screen (Phase 3, UC4/UC7): confidence-coded fields (green = verified/
// high confidence, yellow = low confidence/semantic-verified, red = missing/
// high-stakes-unresolved/verification-failed). One-click approve/edit/approve-blank
// per field, side-by-side with the source document or the blank form. Download stays
// DISABLED until every flagged field is resolved — this is enforced server-side too
// (GET /download 409s until approved); the disabled button is a UX courtesy, not the
// control.

import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import ConfidenceField from "../components/ConfidenceField";
import type { FormReviewOut, ReviewActionResponse } from "../types";

export default function Review() {
  const { id } = useParams<{ id: string }>();
  const [review, setReview] = useState<FormReviewOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [warningDismissed, setWarningDismissed] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const result = await api.getFormReview(id);
      setReview(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load review");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    return () => {
      if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    };
  }, [sourceUrl]);

  function applyResult(result: ReviewActionResponse) {
    setReview((prev) => {
      if (!prev) return prev;
      const fields = prev.fields.map((f) => (f.id === result.field.id ? result.field : f));
      return {
        ...prev,
        status: result.status,
        download_ready: result.download_ready,
        outstanding_fields: fields.filter((f) => f.outstanding).length,
        fields,
      };
    });
  }

  async function handleApprove(fieldId: string) {
    if (!id) return;
    applyResult(await api.submitReview(id, { field_id: fieldId, action: "approve" }));
  }

  async function handleApproveBlank(fieldId: string) {
    if (!id) return;
    applyResult(await api.submitReview(id, { field_id: fieldId, action: "approve_blank" }));
  }

  async function handleCorrect(fieldId: string, value: string, propagate?: boolean) {
    if (!id) return;
    applyResult(
      await api.submitReview(id, {
        field_id: fieldId,
        action: "correct",
        value,
        propagate_to_profile: propagate ?? false,
      }),
    );
  }

  async function handleViewSource(documentId: string) {
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    const blob = await api.getDocumentFile(documentId);
    setSourceUrl(URL.createObjectURL(blob));
  }

  async function handleViewBlankForm() {
    if (!id) return;
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    const blob = await api.getFormFile(id);
    setSourceUrl(URL.createObjectURL(blob));
  }

  async function handleDownload() {
    if (!id || !review) return;
    setDownloading(true);
    setError(null);
    try {
      const blob = await api.downloadForm(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${review.form_type}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  if (!id) return <p className="error">No form selected.</p>;
  if (error) return <p className="error">{error}</p>;
  if (review === null) return <p>Loading…</p>;

  const resolvedCount = review.total_fields - review.outstanding_fields;

  return (
    <div>
      <h1>Review: {review.display_name}</h1>
      <p className="notice">
        This is a draft. It is never submitted anywhere automatically — download the completed
        form and submit it yourself.
      </p>

      {review.placement_warning && !warningDismissed && (
        <div className="warning-banner" data-testid="placement-warning">
          <p>{review.placement_warning}</p>
          <button type="button" className="link" onClick={() => setWarningDismissed(true)}>
            Dismiss
          </button>
        </div>
      )}

      <p>
        {resolvedCount} of {review.total_fields} fields resolved
      </p>

      {review.fields.map((field) => (
        <ConfidenceField
          key={field.id}
          field={field}
          onApprove={handleApprove}
          onApproveBlank={handleApproveBlank}
          onCorrect={handleCorrect}
          onViewSource={(documentId) => void handleViewSource(documentId)}
          showPropagateOption
        />
      ))}

      <div className="review-actions">
        <button type="button" className="link" onClick={() => void handleViewBlankForm()}>
          View blank form
        </button>
        <button
          type="button"
          disabled={!review.download_ready || downloading}
          onClick={() => void handleDownload()}
        >
          {downloading ? "Preparing…" : "Download filled form"}
        </button>
        {!review.download_ready && (
          <p className="notice">Resolve every flagged field to unlock the download.</p>
        )}
      </div>

      {sourceUrl && (
        <div className="source-preview">
          <img src={sourceUrl} alt="Source document" />
          <button type="button" className="link" onClick={() => setSourceUrl(null)}>
            Close
          </button>
        </div>
      )}
    </div>
  );
}
