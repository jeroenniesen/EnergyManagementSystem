// Admin "Access & security" panel (auth slice 2 web, design §7): user list (role change,
// disable/remove) + invite create/list/revoke. Rendered by Settings.tsx INSIDE the "access" nav
// section's content pane (above the legacy web.auth_token/web.require_auth fields), visible only
// when that section is selected AND the current user's role is admin — non-admins never see this
// component at all (Settings gates on `isAdmin` before mounting it), which is what keeps a reader/
// user from being able to reach it even though the backend would 403 anyway (belt + suspenders,
// same "mirrors the API" spirit as the rest of the reader read-only work).
//
// Every write here surfaces a 409 guard refusal (e.g. "cannot demote the last admin") INLINE next
// to the row that triggered it — the controls themselves are never hidden for the acting admin's
// own row; the backend is the one source of truth for what's allowed (CLAUDE.md: don't invent
// client-side permission logic the server doesn't already enforce).
import { useEffect, useState } from "react";

import { apiFetch } from "./auth";

type AdminUser = {
  id: number;
  username: string;
  role: string;
  disabled: number | boolean;
  created_at: string;
  last_login_at: string | null;
};
type UsersResp = { users: AdminUser[] };

type Invite = {
  id: number;
  role: string;
  created_by: number | null;
  created_at: string;
  expires_at: string;
  used_at: string | null;
};
type InvitesResp = { invites: Invite[] };

const ROLES = ["reader", "user", "admin"] as const;

function fmtDate(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export function AdminAccess() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [usersErr, setUsersErr] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<Record<number, boolean>>({});
  const [rowErr, setRowErr] = useState<Record<number, string>>({});

  const [invites, setInvites] = useState<Invite[] | null>(null);
  const [inviteRole, setInviteRole] = useState<string>("user");
  const [creating, setCreating] = useState(false);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [minted, setMinted] = useState<{ url: string; expires_at: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokeBusy, setRevokeBusy] = useState<Record<number, boolean>>({});

  async function loadUsers() {
    try {
      const r = await apiFetch("/api/users");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const b: UsersResp = await r.json();
      setUsers(b.users);
      setUsersErr(null);
    } catch (e) {
      setUsersErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function loadInvites() {
    try {
      const r = await apiFetch("/api/invites");
      if (!r.ok) return;
      const b: InvitesResp = await r.json();
      setInvites(b.invites);
    } catch {
      /* best-effort — the pending list just stays at its last known state */
    }
  }

  useEffect(() => {
    loadUsers();
    loadInvites();
  }, []);

  async function patchUser(u: AdminUser, body: Record<string, unknown>) {
    setRowBusy((p) => ({ ...p, [u.id]: true }));
    setRowErr((p) => ({ ...p, [u.id]: "" }));
    try {
      const r = await apiFetch(`/api/users/${u.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.detail ?? `HTTP ${r.status}`);
      }
      await loadUsers();
    } catch (e) {
      setRowErr((p) => ({ ...p, [u.id]: e instanceof Error ? e.message : String(e) }));
    } finally {
      setRowBusy((p) => ({ ...p, [u.id]: false }));
    }
  }

  async function removeUser(u: AdminUser) {
    setRowBusy((p) => ({ ...p, [u.id]: true }));
    setRowErr((p) => ({ ...p, [u.id]: "" }));
    try {
      const r = await apiFetch(`/api/users/${u.id}`, { method: "DELETE" });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.detail ?? `HTTP ${r.status}`);
      }
      await loadUsers();
    } catch (e) {
      setRowErr((p) => ({ ...p, [u.id]: e instanceof Error ? e.message : String(e) }));
    } finally {
      setRowBusy((p) => ({ ...p, [u.id]: false }));
    }
  }

  async function createInvite() {
    setCreating(true);
    setCreateErr(null);
    setMinted(null);
    setCopied(false);
    try {
      const r = await apiFetch("/api/invites", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ role: inviteRole }),
      });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(b.detail ?? `HTTP ${r.status}`);
      // accept_url is already app-relative ("/#/accept-invite?code=..."); resolve it against the
      // current origin so the copied link is paste-and-go from anywhere.
      const url = new URL(b.accept_url, window.location.origin).toString();
      setMinted({ url, expires_at: b.expires_at });
      await loadInvites();
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  async function copyMintedUrl() {
    if (!minted) return;
    try {
      await navigator.clipboard.writeText(minted.url);
      setCopied(true);
    } catch {
      /* clipboard unavailable (permissions/insecure context) — the selectable input is the
         fallback; the admin can still select-all + copy manually. */
    }
  }

  async function revokeInvite(id: number) {
    setRevokeBusy((p) => ({ ...p, [id]: true }));
    try {
      await apiFetch(`/api/invites/${id}`, { method: "DELETE" });
      await loadInvites();
    } catch {
      /* best-effort — a failed revoke just leaves the row for a retry */
    } finally {
      setRevokeBusy((p) => ({ ...p, [id]: false }));
    }
  }

  const pending = (invites ?? []).filter((i) => !i.used_at);

  return (
    <div className="admin-access" data-testid="admin-access">
      <section className="settings-access-bar" data-testid="admin-users">
        <h2 className="settings-group-title">Users</h2>
        <p className="settings-group-hint">
          Manage who can sign in and what they can do. Removing a user disables their account and
          signs them out everywhere — accounts are never hard-deleted.
        </p>
        {usersErr && (
          <p className="field-err" role="alert">
            Could not load users: {usersErr}
          </p>
        )}
        {users === null && !usersErr ? (
          <p className="loading">Loading users…</p>
        ) : (
          <ul className="admin-list" data-testid="admin-users-list">
            {(users ?? []).map((u) => {
              const disabled = Boolean(u.disabled);
              const busy = Boolean(rowBusy[u.id]);
              return (
                <li key={u.id} className="admin-row" data-testid={`admin-user-${u.id}`}>
                  <div className="admin-row-main">
                    <span className="admin-row-name">{u.username}</span>
                    {disabled && <span className="badge badge-muted">disabled</span>}
                    <span className="admin-row-meta">
                      last login {fmtDate(u.last_login_at)}
                    </span>
                  </div>
                  <div className="admin-row-controls">
                    <span className="admin-row-field-label" aria-hidden="true">Role</span>
                    <select
                      aria-label={`Role for ${u.username}`}
                      value={u.role}
                      disabled={busy}
                      onChange={(e) => patchUser(u, { role: e.target.value })}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={busy}
                      aria-label={`${disabled ? "Enable" : "Disable"} ${u.username}`}
                      onClick={() => patchUser(u, { disabled: !disabled })}
                    >
                      {disabled ? "Enable" : "Disable"}
                    </button>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={busy || disabled}
                      aria-label={`Remove ${u.username}`}
                      onClick={() => removeUser(u)}
                    >
                      Remove
                    </button>
                  </div>
                  {rowErr[u.id] && (
                    <p
                      className="field-err"
                      role="alert"
                      data-testid={`admin-user-error-${u.id}`}
                    >
                      {rowErr[u.id]}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="settings-access-bar" data-testid="admin-invites">
        <h2 className="settings-group-title">Invites</h2>
        <p className="settings-group-hint">
          Invite someone with a one-time link — they choose their own username and password.
        </p>
        <div className="admin-invite-create">
          <label className="admin-row-field-label" htmlFor="admin-invite-role">Role</label>
          <select
            id="admin-invite-role"
            value={inviteRole}
            disabled={creating}
            onChange={(e) => setInviteRole(e.target.value)}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
          <button type="button" className="btn-primary" disabled={creating} onClick={createInvite}>
            {creating ? "Creating…" : "Create invite"}
          </button>
        </div>
        {createErr && (
          <p className="field-err" role="alert">
            {createErr}
          </p>
        )}
        {minted && (
          <div className="admin-invite-minted" data-testid="admin-invite-minted">
            <p className="advisor-hint">
              Share this link — it&apos;s shown only once and works until it expires or is used.
              Expires {fmtDate(minted.expires_at)}.
            </p>
            <div className="admin-invite-url-row">
              <input
                type="text"
                readOnly
                value={minted.url}
                aria-label="Invite link"
                onFocus={(e) => e.target.select()}
              />
              <button type="button" className="btn-ghost" onClick={copyMintedUrl}>
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
        )}

        {pending.length === 0 ? (
          <p className="settings-group-hint">No pending invites.</p>
        ) : (
          <ul className="admin-list" data-testid="admin-invites-list">
            {pending.map((i) => (
              <li key={i.id} className="admin-row" data-testid={`admin-invite-${i.id}`}>
                <div className="admin-row-main">
                  <span className="admin-row-name">{i.role}</span>
                  <span className="admin-row-meta">expires {fmtDate(i.expires_at)}</span>
                </div>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={Boolean(revokeBusy[i.id])}
                  aria-label={`Revoke ${i.role} invite`}
                  onClick={() => revokeInvite(i.id)}
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
