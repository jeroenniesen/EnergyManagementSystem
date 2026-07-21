import { useState } from "react";

import { setToken } from "./auth";

// Forced onboarding (auth slice 1, Task 10): the identity gate serves ONLY this screen until the
// first admin exists (`GET /api/auth`'s `onboarding_needed`). `sharedTokenRequired` comes from that
// same discovery payload — a legacy `EMS_WEB_TOKEN`/`web.auth_token` must be proven (anti-seizure)
// before onboarding is allowed to mint the first admin, so the "Existing access token" field only
// renders when the backend says one is configured.
export function Onboarding({
  sharedTokenRequired,
  onDone,
}: {
  sharedTokenRequired: boolean;
  onDone: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [shared, setShared] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body: Record<string, string> = { username, password };
      if (sharedTokenRequired) body.shared_token = shared;
      const r = await fetch("/api/auth/onboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        setError((await r.json().catch(() => ({}))).detail ?? "Onboarding failed");
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
    <form onSubmit={submit} data-testid="onboarding">
      <h1>Create your admin account</h1>
      <input
        aria-label="Username"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        autoComplete="username"
      />
      <input
        aria-label="Password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="new-password"
      />
      {sharedTokenRequired && (
        <input
          aria-label="Existing access token"
          value={shared}
          onChange={(e) => setShared(e.target.value)}
          autoComplete="off"
        />
      )}
      <button type="submit" className="btn-primary" disabled={busy}>Create admin</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
