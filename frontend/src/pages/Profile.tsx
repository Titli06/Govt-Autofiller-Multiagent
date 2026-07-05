// Profile view: extracted fields grouped by field_name, each confidence-coded with
// confirm/edit controls for flagged candidates, and a source-document preview.

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import ConfidenceField from "../components/ConfidenceField";
import type { ProfileField } from "../types";

export default function Profile() {
  const [fields, setFields] = useState<ProfileField[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const profile = await api.getProfile();
      setFields(profile.fields);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load profile");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    return () => {
      if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    };
  }, [sourceUrl]);

  async function handleConfirm(fieldId: string) {
    const updated = await api.confirmField(fieldId);
    setFields((prev) => prev?.map((f) => (f.id === fieldId ? updated : f)) ?? null);
  }

  async function handleCorrect(fieldId: string, value: string) {
    const updated = await api.correctField(fieldId, value);
    setFields((prev) => prev?.map((f) => (f.id === fieldId ? updated : f)) ?? null);
  }

  async function handleViewSource(documentId: string) {
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    const blob = await api.getDocumentFile(documentId);
    setSourceUrl(URL.createObjectURL(blob));
  }

  if (error) return <p className="error">{error}</p>;
  if (fields === null) return <p>Loading…</p>;

  const grouped = new Map<string, ProfileField[]>();
  for (const field of fields) {
    const list = grouped.get(field.field_name) ?? [];
    list.push(field);
    grouped.set(field.field_name, list);
  }

  return (
    <div>
      <h1>Profile</h1>
      {fields.length === 0 && <p>Upload an ID document to build your profile.</p>}

      {[...grouped.entries()].map(([name, candidates]) => (
        <div key={name} className="profile-field-group">
          {candidates.map((field) => (
            <ConfidenceField
              key={field.id}
              field={field}
              onConfirm={handleConfirm}
              onCorrect={handleCorrect}
              onViewSource={(documentId) => void handleViewSource(documentId)}
            />
          ))}
        </div>
      ))}

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
