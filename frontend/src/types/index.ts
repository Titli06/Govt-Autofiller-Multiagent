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
  // Null for a manual candidate synthesized from a Phase 3 form-review correction —
  // it has no source document.
  document_id: string | null;
  doc_type: DocType | null;
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

// "filled" is retired as of Phase 3 — a zero-flag pipeline lands directly on
// "approved"; any flagged field lands on "in_review".
export type FormStatus = "pending" | "processing" | "in_review" | "approved" | "failed" | "type_mismatch";

export type ReviewReason =
  | "no_mapping"
  | "no_candidate"
  | "verification_failed"
  // Phase 4: every field on an inferred-schema form is always reviewed — a mapping
  // error can't be caught by document verification alone (SPEC-PHASE4.md Decision 1).
  | "inferred_mapping"
  | "high_stakes"
  | "unverified_source"
  | "low_confidence";

// Phase 4: "template" (known-template registry, incl. a confident-detection
// override) | "inferred" (Document AI field detection + semantic label mapping).
export type SchemaSource = "template" | "inferred";

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
  schema_source: SchemaSource;
  fill_error: string | null;
  page_count: number | null;
  created_at: string;
  filled_at: string | null;
  fields: FormFieldOut[];
}

// --- Phase 3: verification + HITL review + download ----------------------------------

export type VerificationMethod = "exact" | "semantic" | "llm" | "user";

export type ReviewActionType = "approve" | "correct" | "approve_blank";

export interface FormFieldReviewOut {
  id: string;
  field_name: string;
  profile_key: string | null;
  display_value: string | null;
  confidence: number;
  confidence_band: ConfidenceBand;
  verified: boolean;
  verification_method: VerificationMethod | null;
  high_stakes: boolean;
  transformed: boolean;
  needs_review: boolean;
  review_reason: ReviewReason | null;
  reviewed: boolean;
  review_action: "approved" | "corrected" | "approved_blank" | null;
  // needs_review AND NOT reviewed — the only thing that blocks download.
  outstanding: boolean;
  source: FormFieldSource;
}

export interface FormReviewOut {
  id: string;
  form_type: FormType;
  display_name: string;
  status: FormStatus;
  schema_source: SchemaSource;
  download_ready: boolean;
  total_fields: number;
  outstanding_fields: number;
  placement_warning: string | null;
  fields: FormFieldReviewOut[];
}

export interface ReviewActionRequest {
  field_id: string;
  action: ReviewActionType;
  value?: string;
  propagate_to_profile?: boolean;
}

export interface ReviewActionResponse {
  field: FormFieldReviewOut;
  status: FormStatus;
  download_ready: boolean;
  warning: string | null;
}
