// Shared TypeScript types mirroring the backend API contract.

export interface User {
  id: string;
  email: string;
  email_verified_at: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user?: User;
}

// FormField — used from Phase 2 onward (review UI). Kept here as the shared contract.
export interface FormField {
  name: string;
  value: string | null;
  sourceDocId: string | null;
  confidence: number;
  needsReview: boolean;
  reviewReason: string | null;
}

// --- Phase 1: profile ingestion from ID documents ---------------------------------

export type DocType = "aadhaar" | "pan";

export type OcrStatus =
  | "pending"
  | "processing"
  | "extracted"
  | "partial"
  | "failed"
  | "type_mismatch";

export type ConfidenceBand = "high" | "medium" | "low";

export type ProfileFieldStatus =
  | "confirmed"
  | "needs_confirmation"
  | "user_confirmed"
  | "user_corrected"
  | "failed_validation";

export interface DocumentUploadResponse {
  document_id: string;
  ocr_status: OcrStatus;
}

export interface DocumentStatus {
  id: string;
  declared_doc_type: DocType;
  detected_doc_type: DocType | null;
  ocr_status: OcrStatus;
  ocr_error: string | null;
  page_count: number | null;
  created_at: string;
  extracted_at: string | null;
}

export interface ProfileFieldSource {
  document_id: string;
  doc_type: DocType;
}

export interface ProfileField {
  id: string;
  field_name: string;
  display_value: string;
  confidence: number;
  confidence_band: ConfidenceBand;
  high_stakes: boolean;
  status: ProfileFieldStatus;
  source: ProfileFieldSource;
}

export interface ProfileOut {
  fields: ProfileField[];
}
