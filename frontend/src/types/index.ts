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

// --- Phase 2: known-template form fill ----------------------------------------------

export type FormType = "income_certificate" | "scholarship_application";

export type FormStatus = "pending" | "processing" | "filled" | "failed" | "type_mismatch";

export type ReviewReason = "no_mapping" | "no_candidate" | "high_stakes" | "unverified_source" | "low_confidence";

export interface FormUploadResponse {
  form_id: string;
  status: FormStatus;
}

export interface FormFieldSource {
  profile_field_id: string | null;
  document_id: string | null;
  doc_type: DocType | null;
}

export interface FormFieldOut {
  id: string;
  field_name: string;
  profile_key: string | null;
  display_value: string | null;
  confidence: number;
  confidence_band: ConfidenceBand;
  high_stakes: boolean;
  transformed: boolean;
  needs_review: boolean;
  review_reason: ReviewReason | null;
  reviewed: boolean;
  source: FormFieldSource;
}

export interface FormOut {
  id: string;
  form_type: FormType;
  display_name: string;
  detected_form_type: string | null;
  status: FormStatus;
  fill_error: string | null;
  page_count: number | null;
  created_at: string;
  filled_at: string | null;
  fields: FormFieldOut[];
}
