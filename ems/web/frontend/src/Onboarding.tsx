import { useState } from "react";

import { AuthLayout } from "./AuthLayout";
import { apiFetch, setToken } from "./auth";

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
  const [created, setCreated] = useState(false);
  const [restartBusy, setRestartBusy] = useState(false);
  const [restartHint, setRestartHint] = useState("");

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
      setCreated(true);
    } catch {
      setError("Couldn't reach the server — try again.");
    } finally {
      setBusy(false);
    }
  }

  async function applyRestart() {
    setRestartHint("");
    setRestartBusy(true);
    try {
      const r = await apiFetch("/api/system/restart", { method: "POST" });
      const b = await r.json().catch(() => ({}));
      if (r.status === 202) {
        setRestartHint("Restarting… reload this page in a few seconds for LAN access.");
        return;
      }
      if (r.status === 409 && b.reason === "not_supervised") {
        setRestartHint(
          "Restart the EMS process manually (install script service or your supervisor) to open LAN access.",
        );
        return;
      }
      setRestartHint(b.detail ?? "Could not restart — try again or restart the process manually.");
    } catch {
      setRestartHint("Could not reach the server — restart the EMS process manually for LAN access.");
    } finally {
      setRestartBusy(false);
    }
  }

  if (created) {
    return (
      <AuthLayout title="Admin account created">
        <p>
          The web UI may still be listening on localhost only until the process restarts. Use{" "}
          <strong>Apply &amp; restart</strong> below (when supervised) or restart the service, then
          open the dashboard from other devices on your LAN.
        </p>
        <div className="field" style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
          <button type="button" className="btn-primary" onClick={() => onDone()}>
            Continue to dashboard
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={restartBusy}
            onClick={() => applyRestart()}
          >
            Apply &amp; restart
          </button>
        </div>
        {restartHint && <p role="status">{restartHint}</p>}
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Create your admin account">
      <form onSubmit={submit} data-testid="onboarding">
        <div className="field">
          <label className="field-label" htmlFor="onboarding-username">Username</label>
          <input
            id="onboarding-username"
            type="text"
            aria-label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="onboarding-password">Password</label>
          <input
            id="onboarding-password"
            aria-label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            minLength={10}
          />
          <p className="field-help">At least 10 characters, with a letter and a digit.</p>
        </div>
        {sharedTokenRequired && (
          <div className="field">
            <label className="field-label" htmlFor="onboarding-shared-token">Existing access token</label>
            <input
              id="onboarding-shared-token"
              type="text"
              aria-label="Existing access token"
              value={shared}
              onChange={(e) => setShared(e.target.value)}
              autoComplete="off"
            />
          </div>
        )}
        <button type="submit" className="btn-primary" disabled={busy}>Create admin</button>
        {error && <p role="alert" className="field-err">{error}</p>}
      </form>
    </AuthLayout>
  );
}
