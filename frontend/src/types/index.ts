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
