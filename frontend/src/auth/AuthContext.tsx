// Auth state for the SPA. On mount it silently calls /refresh (using the httpOnly cookie)
// to rehydrate a session, since the access token is only held in memory and lost on reload.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { api, registerAuthLostHandler } from "../api/client";
import type { User } from "../types";

type Status = "loading" | "authed" | "anon";

interface AuthState {
  status: Status;
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<User | null>(null);

  const goAnon = useCallback(() => {
    setUser(null);
    setStatus("anon");
  }, []);

  useEffect(() => {
    // If a silent refresh/retry ultimately fails anywhere in the app, drop to anon.
    registerAuthLostHandler(goAnon);
  }, [goAnon]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const token = await api.refresh();
      if (cancelled) return;
      if (!token) {
        goAnon();
        return;
      }
      try {
        const me = await api.me();
        if (cancelled) return;
        setUser(me);
        setStatus("authed");
      } catch {
        if (!cancelled) goAnon();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [goAnon]);

  const login = useCallback(async (email: string, password: string) => {
    const u = await api.login(email, password);
    setUser(u);
    setStatus("authed");
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      goAnon();
    }
  }, [goAnon]);

  return (
    <AuthContext.Provider value={{ status, user, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
