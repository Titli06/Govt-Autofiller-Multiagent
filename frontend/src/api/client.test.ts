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
