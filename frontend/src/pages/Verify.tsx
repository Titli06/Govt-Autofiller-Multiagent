import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api/client";

type State = "verifying" | "success" | "error";

export default function Verify() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const [state, setState] = useState<State>("verifying");
  const [message, setMessage] = useState("");
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return; // guard against StrictMode double-invoke
    ran.current = true;

    if (!token) {
      setState("error");
      setMessage("This verification link is missing its token.");
      return;
    }
    (async () => {
      try {
        await api.verifyEmail(token);
        setState("success");
      } catch (err) {
        setState("error");
        setMessage(
          err instanceof ApiError ? err.message : "This verification link is invalid or expired.",
        );
      }
    })();
  }, [token]);

  return (
    <div className="auth-shell">
      {state === "verifying" && <h1>Verifying…</h1>}
      {state === "success" && (
        <>
          <h1>Email verified</h1>
          <p style={{ fontSize: 14 }}>Your account is active.</p>
          <p style={{ fontSize: 13, marginTop: 16 }}>
            <Link to="/login">Continue to login</Link>
          </p>
        </>
      )}
      {state === "error" && (
        <>
          <h1>Verification failed</h1>
          <p className="error">{message}</p>
          <p style={{ fontSize: 13, marginTop: 16 }}>
            <Link to="/login">Back to login</Link> — you can request a new link there.
          </p>
        </>
      )}
    </div>
  );
}
