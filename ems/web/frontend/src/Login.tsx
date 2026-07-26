import { useState } from "react";

import { AuthLayout } from "./AuthLayout";
import { setToken } from "./auth";

// Login gate (auth slice 1, Task 10): shown once onboarding is done but the caller has no valid
// session token (App.tsx's `!auth.authenticated` branch — includes the global-401 bounce-back).
export function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!r.ok) {
        setError("Invalid credentials");
        return;
      }
      setToken((await r.json()).token);
      onDone();
    } catch {
      setError("Couldn't reach the server — try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Sign in">
      <form onSubmit={submit} data-testid="login">
        <div className="field">
          <label className="field-label" htmlFor="login-username">Username</label>
          <input
            id="login-username"
            type="text"
            aria-label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="login-password">Password</label>
          <input
            id="login-password"
            aria-label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <button type="submit" className="btn-primary" disabled={busy}>Sign in</button>
        {error && <p role="alert" className="field-err">{error}</p>}
      </form>
    </AuthLayout>
  );
}
