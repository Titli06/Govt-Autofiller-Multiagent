import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { AuthProvider } from "./auth/AuthContext";

afterEach(() => vi.restoreAllMocks());

describe("ProtectedRoute", () => {
  it("redirects an unauthenticated visitor to the login page", async () => {
    // Silent refresh on mount fails → app resolves to anon → protected "/" redirects to /login.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 401 })),
    );

    render(
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /log in to govfill/i })).toBeDefined(),
    );
  });
});
