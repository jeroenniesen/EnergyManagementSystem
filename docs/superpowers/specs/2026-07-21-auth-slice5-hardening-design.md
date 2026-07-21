# Auth slice 5 — access-token scoping, idle expiry, admin-op session-gating

**Status:** design (approved verbally 2026-07-21) · **Amends:** `2026-07-17-auth-users-roles-design.md` §5 (tier table), §7 (iOS/clients), §9 (hardening) · **Builds on:** slices 1–4 (PR #40, #42, #46)

## 1. Motivation

The P2 adversarial security review of slices 2–4 surfaced three properties that are faithful to
the approved slice-1–4 spec but are latent risks worth closing. They are **design decisions, not
bugs** — this slice revises the design to remove them:

1. **Access tokens inherit their owner's full tier and never expire.** `create_token` stores
   `expires_at = NULL` for `kind='access'` (`ems/storage/auth.py:245`) and `resolve` returns the
   owner's live `role` (`auth.py:293`). So an admin's access token can do anything the admin can —
   including minting an admin invite — forever, until manually revoked.
2. **The migrated shared token is a permanent admin access token.** `onboard_admin` inserts it as
   `kind='access'`, owned by the first admin, `expires_at NULL` (`auth.py:393-399`) — strictly
   better than the prior all-powerful shared secret (now named + revocable), but still admin-tier
   and non-expiring.
3. **The iOS widget token lives in app-group `UserDefaults`, not the Keychain** (deliberate — see
   `WidgetSupport.swift`; Keychain *sharing* needs a `keychain-access-groups` entitlement the
   project doesn't carry). Acceptable *iff* that token is low-privilege.

These interact: if the widget token is **scoped read-only**, "an admin credential sits in
UserDefaults" stops being true — the sharpest concern (3) is dissolved by fixing (1). That is the
approach this slice takes.

## 2. Decisions (locked with the user 2026-07-21)

| Fork | Decision |
| --- | --- |
| Access-token privilege | **Per-token tier** (mint an access token at a chosen tier ≤ owner, default read-only) **and** session-gate `/api/users*` + `/api/invites*` so no access token can manage accounts. |
| Access-token expiry | **Idle auto-revoke**: an access token unused for `auth.access_token_idle_days` (default 90) stops resolving. A live widget stays alive; a forgotten/leaked token ages out. |
| iOS widget storage | **Keep app-group `UserDefaults`** — justified because the widget token is now scoped read-only. No entitlements/provisioning change. |
| Default mint tier | **read-only (`view`)**; callers opt up, hard-capped at the owner's role. |
| Legacy / NULL-tier access tokens | Resolve capped at **OPERATE, not ADMIN** — removes latent admin-via-old-token while preserving write automation. |
| Migrated shared token | Minted at **OPERATE** tier explicitly. |

## 3. Data model

Add one nullable column to `auth_tokens`:

```
tier TEXT CHECK(tier IS NULL OR tier IN ('view','operate','admin'))
```

- **Fresh installs:** the column is added to `_TOKENS_DDL` so `CREATE TABLE IF NOT EXISTS` creates
  it directly.
- **Existing installs:** an **idempotent migration** in `AuthStore.init()` adds it — the store today
  only does `CREATE TABLE IF NOT EXISTS`, so this introduces a tiny column-migration path:

  ```python
  cur = await db.execute("PRAGMA table_info(auth_tokens)")
  cols = {r[1] for r in await cur.fetchall()}
  if "tier" not in cols:
      await db.execute(
          "ALTER TABLE auth_tokens ADD COLUMN tier TEXT "
          "CHECK(tier IS NULL OR tier IN ('view','operate','admin'))"
      )
  ```

  (If SQLite rejects the CHECK on `ADD COLUMN` in the target version, drop the CHECK from the ALTER
  and rely on app-level validation + the fresh-install DDL CHECK — verify at implementation time.)

Semantics of the column:
- `tier` is meaningful **only for `kind='access'`**. Session tokens leave it `NULL` and resolve at
  the owner's full role (a session is the interactive user acting as themselves).
- `tier` is stored in **tier vocabulary** (`view`/`operate`/`admin`), which maps 1:1 by rank to the
  role vocabulary (`reader`/`user`/`admin`) already in `authz._ROLE_RANK` and `authz.Tier`.

## 4. Effective-tier policy (the one place privilege is decided)

Keep `AuthStore.resolve()` returning **raw facts** (owner role, token kind, token tier) — the store
stays ignorant of tier-vocabulary ranking. Centralize the policy in `ems/web/authz.py`, next to
`Tier` and `_ROLE_RANK`:

```python
_TIER_RANK = {"view": 0, "operate": 1, "admin": 2}          # aligns with Tier enum ranks
_LEGACY_ACCESS_CAP = _TIER_RANK["operate"]                  # NULL-tier access tokens cap here

def effective_rank(role: str, kind: str, token_tier: str | None) -> int:
    owner = _ROLE_RANK.get(role, -1)
    if kind == "session":
        return owner                                        # session = full owner role
    cap = _TIER_RANK[token_tier] if token_tier else _LEGACY_ACCESS_CAP
    return min(owner, cap)                                  # access = min(owner, scope)
```

- `min(owner, cap)` means **demoting the owner also demotes their tokens** (checked live every
  request), and an over-privileged stored tier can never exceed the owner — defense in depth behind
  the mint-time validation in §5.
- Legacy access tokens (`tier IS NULL`, minted before this slice, including any pre-existing
  "Migrated shared token") resolve at `min(owner, operate)` — the approved backward-compat rule.
  This **silently downgrades** any existing admin-tier access token to operate; that is intended.

`Principal` gains one field: `token_tier: str | None` (the raw `auth_tokens.tier`). `role` still
carries the owner's live role for identity/display.

## 5. Backend changes

### 5.1 `ems/storage/auth.py`
- **Schema:** add `tier` to `_TOKENS_DDL` + the idempotent ALTER in `init()` (§3).
- **`Principal`:** add `token_tier: str | None`.
- **`create_token(user_id, kind, *, name=None, tier=None)`:** persist `tier` (only for access;
  ignored/`NULL` for session). Include `tier` in the INSERT column list.
- **`replace_token(user_id, name, *, tier="view")`:** the widget/automation remint path — default
  **read-only**. Persist `tier` in its INSERT (currently hard-codes the column list at
  `auth.py:349-352`).
- **`onboard_admin`:** the migrated-token INSERT (`auth.py:394-399`) sets `tier='operate'`.
- **`resolve()`:** two additions, both **before** the throttled `last_used_at` write so the hot path
  is unchanged for live tokens:
  1. **SELECT** `t.created_at` and `t.tier` (in addition to the current columns).
  2. **Idle rejection (access only):** let `last_activity = last_used_at or created_at`; if
     `kind == 'access'` and `now - last_activity > self._access_idle_ttl`, return `None`. (A live
     token bumps `last_used_at` on each resolve, so it never idles; a fresh token uses `created_at`
     and is never immediately idle.)
  3. Return `Principal(..., token_tier=row["tier"])`.
- **Construction:** `AuthStore(db_path, *, access_token_idle_days: int = 90)` →
  `self._access_idle_ttl = timedelta(days=access_token_idle_days)`. Wire the value from config at the
  construction site in `api.py`.
- **Hygiene (optional, non-authoritative):** add `purge_idle_access_tokens()` deleting access rows
  whose `last_activity` exceeds the idle TTL. Wire it into the **existing** periodic maintenance if
  one exists; **do not add a new background loop** for this slice. Lazy rejection in `resolve()` is
  the security-enforcing mechanism regardless.

### 5.2 `ems/web/authz.py`
- Add `_TIER_RANK`, `_LEGACY_ACCESS_CAP`, `effective_rank()` (§4).
- Extend `_SESSION_ONLY_PREFIXES` to `("/api/auth/tokens", "/api/users", "/api/invites")`.
  `/api/invites/accept` stays reachable logged-out because the exempt check in `api.py:1471` runs
  **before** `requires_session` (it's in `EXEMPT_PATHS`) — verify the ordering holds.

### 5.3 `ems/web/api.py` (the gate, `~1501`)
- Replace `role_satisfies(principal.role, required_tier(path, method))` with
  `effective_rank(principal.role, principal.kind, principal.token_tier) >= int(required_tier(path, method))`.
- The `requires_session(...)` check below it is unchanged; with §5.2 it now also rejects access
  tokens on `/api/users*` and `/api/invites*` (they were ADMIN-tier already; now they are ADMIN
  **and** session-only).
- **Audit-category admin check (`api.py:3546`):** change `is_admin` to use effective tier
  (`effective_rank(...) >= int(Tier.ADMIN)`) so an admin acting through an OPERATE-scoped access
  token does **not** see stripped auth-audit rows — consistent least-privilege. (Admin **sessions**
  are unaffected: effective == admin.)

### 5.4 `/api/auth/tokens` mint route (`ems/web/routes/auth.py`)
- Accept optional `tier` in the POST body (default `"view"`). **Validate** the requested tier's rank
  ≤ the caller's owner-role rank; reject over-requests with **400** (explicit, not silent clamp).
  Pass `tier` through to `create_token` / `replace_token`.
- `GET /api/auth/tokens` (list) includes each token's `tier` so the UI can display it.

### 5.5 `ems/config.py` / `config.yaml`
- New default under `auth`: `access_token_idle_days: 90` (read-only default; overridable per the
  config convention). Surfaced to `AuthStore` construction.

## 6. Frontend changes

- **Account → API tokens panel:** the mint form gains a **tier selector** (default *Read-only*;
  offer only tiers ≤ the current user's own role). Send `tier` in the mint POST.
- Show each token's tier in the token list (a small badge: *Read-only* / *Operate* / *Admin*).
- No change to reader-mode gating: the web UI always authenticates with a **session** (full owner
  role), so `canOperate` and the existing reader hints are unaffected. Scoped tokens are for
  machines/the widget, never the interactive SPA.

## 7. iOS changes

- **Widget-token provisioning** (`DashboardStore.login()` → `POST /api/auth/tokens {replace:true}`):
  add `tier: "view"` to the request body. The widget only reads dashboard data (VIEW), so read-only
  is sufficient and is what makes keeping the token in app-group `UserDefaults` acceptable.
- **No entitlements/provisioning change** — the `keychain-access-groups` move is explicitly out of
  scope (the whole point of scoping the token). Update the `WidgetSupport.swift` rationale comment to
  reflect that the stored token is now read-only.
- The interactive app continues to use its **session** token for the user's own actions — unchanged.

## 8. Testing

Backend (`ems/tests/`):
- Migration is idempotent: `init()` twice on a DB created without `tier` adds the column once; a
  fresh DB has it from the DDL.
- **Scope capping:** an access token minted at `view` gets 403 on an OPERATE path; at `operate`
  succeeds on OPERATE, 403 on ADMIN. Effective tier is `min(owner, scope)`: an admin-owned `view`
  token resolves as VIEW; an admin-owned `admin`-scoped token whose owner is then demoted to `user`
  resolves as OPERATE (the owner now caps it). Assert the `min` directly.
- **Legacy NULL tier:** a token row with `tier IS NULL` (simulating a pre-slice token) resolves at
  `min(owner, operate)` — an admin-owned NULL-tier access token cannot reach an ADMIN path.
- **Session-gate:** an access token (any tier) gets 403 on `POST /api/users`, `POST /api/invites`;
  a session succeeds. `POST /api/invites/accept` remains reachable **unauthenticated**.
- **Idle rejection:** an access token whose `last_used_at` (or `created_at` when null) is older than
  the idle TTL resolves to `None`; a fresh/recently-used one resolves; a session past the idle
  window is unaffected by the access-only rule (it has its own 30-day expiry).
- **Mint validation:** requesting a tier above the caller's role returns 400; the migrated shared
  token is created at `operate`.
- **Audit visibility:** an admin on an operate-scoped access token gets auth-category rows stripped
  from `/api/audit`; an admin session still sees them.

Frontend (Playwright): the tier selector renders, defaults to read-only, offers only tiers ≤ own;
a minted token shows its tier badge. Existing reader-mode specs stay green (session-based).

iOS (`swift test`): the provisioning request body includes `tier:"view"`; existing widget
write-path pinning tests still pass (the widget token remains an explicit access token).

## 9. Non-goals (YAGNI)

- No `keychain-access-groups` entitlement / Keychain move (dissolved by read-only scoping).
- No absolute-TTL expiry or client silent-re-mint machinery (idle-revoke was chosen instead).
- No per-endpoint OAuth-style scopes — a single tier per token is sufficient at single-home scale.
- No new background loop solely for idle purge (lazy rejection enforces the property).

## 10. Rollout

Additive and backward-compatible: existing sessions and access tokens keep working (access tokens
capped at operate until re-minted). The one visible behavior change is that a **pre-existing
admin-tier access token loses admin** — acceptable and intended; the migrated shared token is the
only such token in practice and it does not need admin (it can't manage users anyway once
session-gated). Ships on branch `feat/auth-slice5`, PR based on `feat/auth-slices-2-4`.
