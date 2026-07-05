import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";

export default function Register() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setSubmitting(true);
    try {
      await api.register(email, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="auth-shell">
        <h1>Check your email</h1>
        <p style={{ fontSize: 14 }}>
          We've sent a verification link to <strong>{email}</strong>. Click it to activate your
          account, then log in.
        </p>
        <p style={{ fontSize: 13, marginTop: 16 }}>
          <Link to="/login">Back to login</Link>
        </p>
      </div>
    );
  }

  return (
    <div className="auth-shell">
      <h1>Create your GovFill account</h1>
      <form onSubmit={onSubmit}>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
        </div>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Creating…" : "Register"}
        </button>
      </form>
      <p style={{ fontSize: 13, marginTop: 16 }}>
        Already have an account? <Link to="/login">Log in</Link>
      </p>
    </div>
  );
}
