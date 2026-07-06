// Typed REST client for the FastAPI backend.
//
// Session model (SPEC.md §2, §11): the access token lives only in memory (this module),
// attached as `Authorization: Bearer` on every request. The refresh token rides in an
// httpOnly cookie the browser sends automatically (credentials: "include"). On a 401 we
// attempt one silent /refresh and retry; if that fails, onAuthLost() notifies the app.

import type {
  DocType,
  DocumentStatus,
  DocumentUploadResponse,
  FormOut,
  FormType,
  FormUploadResponse,
  ProfileField,
  ProfileOut,
  TokenResponse,
  User,
} from "../types";

const BASE = "/api";

let accessToken: string | null = null;
let onAuthLost: (() => void) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function registerAuthLostHandler(handler: () => void): void {
  onAuthLost = handler;
}

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function parseError(status: number, data: unknown): ApiError {
  // Our routes return { detail: { detail, code } }; FastAPI validation returns
  // { detail: [ ... ] }; fall back to a generic message otherwise.
  const detail = (data as { detail?: unknown })?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as { detail?: string; code?: string };
    return new ApiError(status, d.detail ?? "Request failed", d.code);
  }
  if (Array.isArray(detail)) {
    return new ApiError(status, "Please check the form and try again.", "VALIDATION_ERROR");
  }
  if (typeof detail === "string") return new ApiError(status, detail);
  return new ApiError(status, "Request failed");
}

async function rawRequest(path: string, options: RequestInit): Promise<Response> {
  const headers = new Headers(options.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  // FormData sets its own multipart boundary — never override it with JSON.
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(BASE + path, { ...options, headers, credentials: "include" });
}

async function request<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  let res = await rawRequest(path, options);

  if (res.status === 401 && retry && path !== "/auth/refresh") {
    const refreshed = await trySilentRefresh();
    if (refreshed) {
      res = await rawRequest(path, options);
    } else {
      onAuthLost?.();
    }
  }

  if (res.status === 204) return undefined as T;

  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw parseError(res.status, data);
  return data as T;
}

async function trySilentRefresh(): Promise<boolean> {
  try {
    const res = await rawRequest("/auth/refresh", { method: "POST" });
    if (!res.ok) return false;
    const data = (await res.json()) as TokenResponse;
    setAccessToken(data.access_token);
    return true;
  } catch {
    return false;
  }
}

export const api = {
  register: (email: string, password: string) =>
    request<{ message: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  verifyEmail: (token: string) =>
    request<{ message: string }>("/auth/verify-email", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  resendVerification: (email: string) =>
    request<{ message: string }>("/auth/resend-verification", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  login: async (email: string, password: string): Promise<User> => {
    const data = await request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setAccessToken(data.access_token);
    return data.user as User;
  },

  // Rehydrate a session on page load using the httpOnly refresh cookie. Returns the
  // access token on success, or null if there's no valid session.
  refresh: async (): Promise<string | null> => {
    const ok = await trySilentRefresh();
    return ok ? accessToken : null;
  },

  logout: async (): Promise<void> => {
    await request<void>("/auth/logout", { method: "POST" });
    setAccessToken(null);
  },

  me: () => request<User>("/auth/me"),

  uploadDocument: (file: File, docType: DocType): Promise<DocumentUploadResponse> => {
    const body = new FormData();
    body.append("file", file);
    body.append("doc_type", docType);
    return request<DocumentUploadResponse>("/documents/upload", { method: "POST", body });
  },

  getDocumentStatus: (documentId: string) =>
    request<DocumentStatus>(`/documents/${documentId}/status`),

  // Fetched (not a plain <img src>) because the file endpoint is Bearer-authenticated,
  // not cookie-authenticated — callers turn the blob into an object URL for display.
  getDocumentFile: async (documentId: string): Promise<Blob> => {
    const res = await rawRequest(`/documents/${documentId}/file`, { method: "GET" });
    if (!res.ok) throw new ApiError(res.status, "Failed to load source document");
    return res.blob();
  },

  getProfile: () => request<ProfileOut>("/profile"),

  confirmField: (fieldId: string) =>
    request<ProfileField>(`/profile/fields/${fieldId}/confirm`, { method: "POST" }),

  correctField: (fieldId: string, value: string) =>
    request<ProfileField>(`/profile/fields/${fieldId}/correct`, {
      method: "POST",
      body: JSON.stringify({ value }),
    }),

  uploadForm: (file: File, formType: FormType): Promise<FormUploadResponse> => {
    const body = new FormData();
    body.append("file", file);
    body.append("form_type", formType);
    return request<FormUploadResponse>("/forms/upload", { method: "POST", body });
  },

  getForm: (formId: string) => request<FormOut>(`/forms/${formId}`),
};
