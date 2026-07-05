import { afterEach, describe, expect, it, vi } from "vitest";

import { api, setAccessToken } from "./client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  setAccessToken(null);
});

describe("api client", () => {
  it("attaches the bearer token to requests", async () => {
    setAccessToken("abc123");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: "1" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.me();

    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer abc123");
    expect(init.credentials).toBe("include");
  });

  it("on 401 attempts a silent refresh once, then retries the original request", async () => {
    setAccessToken("stale");
    const fetchMock = vi
      .fn()
      // 1) original /auth/me → 401
      .mockResolvedValueOnce(jsonResponse(401, { detail: { detail: "expired", code: "X" } }))
      // 2) /auth/refresh → 200 with a new token
      .mockResolvedValueOnce(jsonResponse(200, { access_token: "fresh", token_type: "bearer" }))
      // 3) retried /auth/me → 200
      .mockResolvedValueOnce(jsonResponse(200, { id: "1", email: "a@b.com" }));
    vi.stubGlobal("fetch", fetchMock);

    const me = await api.me();

    expect(me.email).toBe("a@b.com");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/refresh");
    // The retry carries the refreshed bearer token.
    const retryHeaders = new Headers(fetchMock.mock.calls[2][1].headers);
    expect(retryHeaders.get("Authorization")).toBe("Bearer fresh");
  });

  it("throws an ApiError carrying the backend code", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(403, { detail: { detail: "Email not verified", code: "EMAIL_NOT_VERIFIED" } }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.login("a@b.com", "pw")).rejects.toMatchObject({
      status: 403,
      code: "EMAIL_NOT_VERIFIED",
    });
  });
});

describe("Phase 1: documents + profile", () => {
  it("uploadDocument sends multipart form data without a JSON content-type", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(202, { document_id: "doc-1", ocr_status: "pending" }));
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["fake-bytes"], "aadhaar.jpg", { type: "image/jpeg" });
    const result = await api.uploadDocument(file, "aadhaar");

    expect(result).toEqual({ document_id: "doc-1", ocr_status: "pending" });
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/documents/upload");
    expect(init.body).toBeInstanceOf(FormData);
    const headers = new Headers(init.headers);
    expect(headers.has("Content-Type")).toBe(false); // browser sets the multipart boundary
    const body = init.body as FormData;
    expect(body.get("doc_type")).toBe("aadhaar");
    expect(body.get("file")).toBe(file);
  });

  it("getDocumentStatus requests the status endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        id: "doc-1",
        declared_doc_type: "aadhaar",
        detected_doc_type: "aadhaar",
        ocr_status: "extracted",
        ocr_error: null,
        page_count: 1,
        created_at: "2026-01-01T00:00:00Z",
        extracted_at: "2026-01-01T00:00:05Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getDocumentStatus("doc-1");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/doc-1/status");
    expect(result.ocr_status).toBe("extracted");
  });

  it("getDocumentFile returns a blob and throws ApiError on failure", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("bytes", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const blob = await api.getDocumentFile("doc-1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/doc-1/file");
    expect(blob).toBeInstanceOf(Blob);

    const failMock = vi.fn().mockResolvedValue(new Response(null, { status: 404 }));
    vi.stubGlobal("fetch", failMock);
    await expect(api.getDocumentFile("missing")).rejects.toMatchObject({ status: 404 });
  });

  it("getProfile fetches the field list", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { fields: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getProfile();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/profile");
    expect(result).toEqual({ fields: [] });
  });

  it("confirmField posts to the confirm endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        id: "field-1",
        field_name: "full_name",
        display_value: "Rajesh Kumar",
        confidence: 0.9,
        confidence_band: "high",
        high_stakes: false,
        status: "user_confirmed",
        source: { document_id: "doc-1", doc_type: "aadhaar" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.confirmField("field-1");

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/profile/fields/field-1/confirm");
    expect(init.method).toBe("POST");
    expect(result.status).toBe("user_confirmed");
  });

  it("correctField posts the corrected value as JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        id: "field-1",
        field_name: "aadhaar_number",
        display_value: "XXXX XXXX 2346",
        confidence: 1.0,
        confidence_band: "high",
        high_stakes: true,
        status: "user_corrected",
        source: { document_id: "doc-1", doc_type: "aadhaar" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.correctField("field-1", "234123412346");

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/profile/fields/field-1/correct");
    expect(JSON.parse(init.body as string)).toEqual({ value: "234123412346" });
    expect(result.status).toBe("user_corrected");
  });
});
