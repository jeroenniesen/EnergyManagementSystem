// Account "API tokens" panel (auth slice 3 web, design §5/§7): long-lived ACCESS token mint/list/
// revoke. Rendered in Settings.tsx's "Account" bar, beside the logout button — visible to EVERY
// signed-in role (design §5's tier table: any role, reader included, manages its OWN tokens; this
// is deliberately NOT gated on `canOperate`/`isAdmin` the way AdminAccess is).
//
// Credential management is session-only, though (authz.requires_session covers the whole
// /api/auth/tokens* prefix): a machine/access token — e.g. the e2e "app" project's migrated shared
// token, or the iOS widget's own token — must not be able to mint/list/revoke tokens, even its
// own, so a leaked machine token can't be used to mint itself new, longer-lived credentials. We
// learn our OWN kind from `GET /api/auth/me` (not the `/api/auth` discovery endpoint the rest of
// the app uses, which never carries `kind`) and render a quiet hint instead of the manage UI when
// it isn't `"session"`.
import { useEffect, useState } from "react";

import { apiFetch } from "./auth";
import { useCopyToClipboard } from "./useCopyToClipboard";

type ApiToken = {
  id: number;
  kind: string;
  name: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
};
type TokensResp = { tokens: ApiToken[] };

function fmtDate(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export function AccountTokens() {
  // null = not yet known (GET /api/auth/me in flight) — nothing renders meanwhile, so a session
  // user never sees a flash of the "sign in with your password" hint before the manage UI mounts.
  const [kind, setKind] = useState<string | null>(null);
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [listErr, setListErr] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [minting, setMinting] = useState(false);
  const [mintErr, setMintErr] = useState<string | null>(null);
  const [minted, setMinted] = useState<string | null>(null);
  const { copied, copy: copyToClipboard } = useCopyToClipboard();
  const [revokeBusy, setRevokeBusy] = useState<Record<number, boolean>>({});

  async function loadKind() {
    try {
      const r = await apiFetch("/api/auth/me");
      if (!r.ok) return;
      const b = await r.json();
      setKind(typeof b.kind === "string" ? b.kind : "unknown");
    } catch {
      setKind("unknown"); // fail toward the hint, never toward exposing the manage UI
    }
  }

  async function loadTokens() {
    try {
      const r = await apiFetch("/api/auth/tokens");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const b: TokensResp = await r.json();
      setTokens(b.tokens);
      setListErr(null);
    } catch (e) {
      setListErr(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    loadKind();
  }, []);

  useEffect(() => {
    if (kind === "session") loadTokens();
  }, [kind]);

  async function mint() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setMinting(true);
    setMintErr(null);
    setMinted(null);
    try {
      const r = await apiFetch("/api/auth/tokens", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(b.detail ?? `HTTP ${r.status}`);
      setMinted(b.token);
      setName("");
      await loadTokens();
    } catch (e) {
      setMintErr(e instanceof Error ? e.message : String(e));
    } finally {
      setMinting(false);
    }
  }

  async function copyMinted() {
    if (!minted) return;
    await copyToClipboard(minted);
  }

  async function revoke(id: number) {
    setRevokeBusy((p) => ({ ...p, [id]: true }));
    try {
      await apiFetch(`/api/auth/tokens/${id}`, { method: "DELETE" });
      await loadTokens();
    } catch {
      /* best-effort — a failed revoke just leaves the row for a retry */
    } finally {
      setRevokeBusy((p) => ({ ...p, [id]: false }));
    }
  }

  if (kind === null) return null; // GET /api/auth/me still in flight

  if (kind !== "session") {
    return (
      <div className="settings-access-bar" data-testid="account-tokens">
        <h2 className="settings-group-title">API tokens</h2>
        <p className="settings-group-hint" data-testid="account-tokens-hint">
          Sign in with your password to manage API tokens — machine tokens can&apos;t manage
          credentials.
        </p>
      </div>
    );
  }

  return (
    <div className="settings-access-bar" data-testid="account-tokens">
      <h2 className="settings-group-title">API tokens</h2>
      <p className="settings-group-hint">
        Long-lived tokens for scripts, widgets, and other machines to use instead of your
        password. Each is independently revocable.
      </p>

      <div className="admin-invite-create">
        <label className="admin-row-field-label" htmlFor="account-token-name">Name</label>
        <input
          id="account-token-name"
          type="text"
          value={name}
          placeholder="e.g. iOS widget"
          disabled={minting}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") mint();
          }}
        />
        <button
          type="button"
          className="btn-primary"
          disabled={minting || !name.trim()}
          onClick={mint}
        >
          {minting ? "Creating…" : "Create"}
        </button>
      </div>
      {mintErr && (
        <p className="field-err" role="alert">
          {mintErr}
        </p>
      )}
      {minted && (
        <div className="admin-invite-minted" data-testid="account-token-minted" role="status">
          <p className="advisor-hint">
            Copy it now — it&apos;s shown only once and can&apos;t be retrieved again.
          </p>
          <div className="admin-invite-url-row">
            <input
              type="text"
              readOnly
              value={minted}
              aria-label="New API token"
              onFocus={(e) => e.target.select()}
            />
            <button type="button" className="btn-ghost" onClick={copyMinted}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
      )}

      {listErr && (
        <p className="field-err" role="alert">
          Couldn&apos;t load tokens: {listErr}
        </p>
      )}
      {tokens === null && !listErr ? (
        <p className="loading">Loading tokens…</p>
      ) : (tokens ?? []).length === 0 ? (
        <p className="settings-group-hint">No API tokens yet.</p>
      ) : (
        <ul className="admin-list" data-testid="account-tokens-list">
          {(tokens ?? []).map((t) => (
            <li key={t.id} className="admin-row" data-testid={`account-token-${t.id}`}>
              <div className="admin-row-main">
                <span className="admin-row-name">{t.name ?? "session"}</span>
                <span className="admin-row-meta">created {fmtDate(t.created_at)}</span>
                <span className="admin-row-meta">last used {fmtDate(t.last_used_at)}</span>
              </div>
              <button
                type="button"
                className="btn-ghost"
                disabled={Boolean(revokeBusy[t.id])}
                aria-label={`Revoke ${t.name ?? "session"}`}
                onClick={() => revoke(t.id)}
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
