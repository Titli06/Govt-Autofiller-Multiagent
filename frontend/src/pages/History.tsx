// History dashboard (Phase 5, UC5/FR11): past filled forms, newest first, each
// deep-linking to the existing Review/download flows rather than duplicating them
// (SPEC-PHASE5.md Decision 6). Also hosts the explicit, password-confirmed
// delete-my-data flow (Decision 1/3) — a data-only purge; the account/session
// survive, so the page just clears its own local state on success.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { DeleteProfileResponse, HistoryItem } from "../types";

const STATUS_LABEL: Partial<Record<string, string>> = {
  in_review: "Needs review",
  approved: "Approved",
  failed: "Failed",
  type_mismatch: "Type mismatch",
};

export default function History() {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteResult, setDeleteResult] = useState<DeleteProfileResponse | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await api.getHistory();
      setItems(result.forms);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load history");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleDownload(formId: string) {
    setDownloadingId(formId);
    setError(null);
    try {
      const blob = await api.downloadForm(formId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${formId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Download failed");
    } finally {
      setDownloadingId(null);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      const result = await api.deleteMyData(password);
      setDeleteResult(result);
      setItems([]);
      setConfirmOpen(false);
      setPassword("");
    } catch (err) {
      if (err instanceof ApiError && err.code === "INVALID_PASSWORD") {
        setDeleteError("Password is incorrect.");
      } else if (err instanceof ApiError && err.code === "JOBS_IN_PROGRESS") {
        setDeleteError("A document or form is still processing; try again in a moment.");
      } else {
        setDeleteError(err instanceof ApiError ? err.message : "Failed to delete your data.");
      }
    } finally {
      setDeleting(false);
    }
  }

  if (error) return <p className="error">{error}</p>;
  if (items === null) return <p>Loading…</p>;

  return (
    <div>
      <h1>History</h1>

      {deleteResult && (
        <p className="notice" data-testid="delete-success">
          Your data was deleted: {deleteResult.forms_deleted} form(s),{" "}
          {deleteResult.documents_deleted} document(s), and {deleteResult.profile_fields_deleted}{" "}
          profile field(s) removed.
        </p>
      )}

      {items.length === 0 ? (
        <p>
          No forms yet. <Link to="/forms">Fill your first form</Link>.
        </p>
      ) : (
        <ul className="history-list">
          {items.map((item) => (
            <li key={item.id} className="history-item" data-testid={`history-item-${item.id}`}>
              <div className="history-item-header">
                <strong>{item.display_name}</strong>
                {item.schema_source === "inferred" && (
                  <span className="badge" data-testid="schema-source-badge">
                    Auto-detected
                  </span>
                )}
                <span className="badge">{STATUS_LABEL[item.status] ?? item.status}</span>
              </div>
              <div className="history-item-meta">
                <span>{new Date(item.created_at).toLocaleDateString()}</span>
                <span>
                  {item.total_fields - item.outstanding_fields} of {item.total_fields} fields
                  resolved
                </span>
              </div>

              {item.status === "approved" && (
                <button
                  type="button"
                  disabled={downloadingId === item.id}
                  onClick={() => void handleDownload(item.id)}
                >
                  {downloadingId === item.id ? "Preparing…" : "Download"}
                </button>
              )}
              {item.status === "in_review" && (
                <Link to={`/forms/${item.id}/review`}>Continue review</Link>
              )}
              {(item.status === "failed" || item.status === "type_mismatch") && (
                <p className="notice">{item.fill_error ?? "This form couldn't be completed."}</p>
              )}
            </li>
          ))}
        </ul>
      )}

      <section className="danger-zone">
        <h2>Delete all my data</h2>
        <p>
          This permanently deletes your profile, every uploaded document, and every form —
          including downloaded PDFs. This cannot be undone. Your account stays active, so you can
          start fresh right away.
        </p>

        {!confirmOpen ? (
          <button type="button" className="danger" onClick={() => setConfirmOpen(true)}>
            Delete all my data
          </button>
        ) : (
          <div className="confirm-delete" data-testid="delete-modal">
            <p>
              <strong>This cannot be undone.</strong> Enter your password to confirm permanent
              deletion of your profile, documents, and forms.
            </p>
            <label htmlFor="delete-password">Password</label>
            <input
              id="delete-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
            {deleteError && <p className="error">{deleteError}</p>}
            <button
              type="button"
              className="danger"
              disabled={!password || deleting}
              onClick={() => void handleDelete()}
            >
              {deleting ? "Deleting…" : "Permanently delete everything"}
            </button>
            <button
              type="button"
              className="link"
              onClick={() => {
                setConfirmOpen(false);
                setPassword("");
                setDeleteError(null);
              }}
            >
              Cancel
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
