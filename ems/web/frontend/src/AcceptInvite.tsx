import { useState } from "react";

import { setToken } from "./auth";

// Invite-accept flow (auth slice 2 web, design §7): `/#/accept-invite?code=<raw>` must be
// reachable while logged out — App.tsx renders this BEFORE the login gate (mirroring Onboarding),
// so a fresh browser with no token still lands here instead of bouncing to <Login/>. Pattern-
// matches Onboarding.tsx: plain (non-apiFetch) POST since this endpoint is EXEMPT.
function parseInviteCode(hash: string): string {
  const qIdx = hash.indexOf("?");
  if (qIdx < 0) return "";
  return new URLSearchParams(hash.slice(qIdx + 1)).get("code") ?? "";
}

export function AcceptInvite({ onDone }: { onDone: () => void }) {
  // Read once on mount — the code lives in the hash that got us here; it never changes mid-flow.
  const [code] = useState(() => parseInviteCode(window.location.hash));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const r = await fetch("/api/invites/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, username, password }),
      });
      if (!r.ok) {
        if (r.status === 409) {
          setError("That username is already taken.");
        } else if (r.status === 401) {
          setError("This invite is invalid or has expired.");
        } else {
          const b = await r.json().catch(() => ({}));
          setError(b.detail ?? "Couldn't accept the invite");
        }
        return;
      }
      const b = await r.json();
      setToken(b.token);
      // Wait for the auth discovery re-fetch to resolve BEFORE flipping the hash, so the app
      // renders straight into the dashboard rather than flashing <Login/> for one frame (the hash
      // change is what makes App.tsx stop treating this as the accept-invite route).
      await Promise.resolve(onDone());
      window.location.hash = "dashboard";
    } catch {
      setError("Couldn't reach the server — try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} data-testid="accept-invite">
      <h1>Join your home&apos;s EMS</h1>
      <p>Set a username and password to finish creating your account.</p>
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
      <button type="submit" className="btn-primary" disabled={busy}>
        Create account
      </button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
