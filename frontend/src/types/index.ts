// Shared TypeScript types mirroring the backend API contract.

export interface FormField {
  name: string;
  value: string | null;
  sourceDocId: string | null;
  confidence: number;
  needsReview: boolean;
  reviewReason: string | null;
}
