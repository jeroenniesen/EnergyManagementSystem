# Design — Username/password auth with users & roles

*Brainstormed 2026-07-17. Owner: Jeroen. Feeds a `writing-plans` implementation plan.*
*Backlog: extends **B-83** (secure deployment posture) and the E-07 consumer-ready epic; a
prerequisite for **B-39** (secure remote access).*
*Revised 2026-07-17 after design review — round 1: findings 1–6 (principal `kind`, fresh-DB
baseline, per-device widget token, transactional guards, session-refresh policy, token
owner-scoping). Round 2: shared-token migration folded into the onboarding transaction; token
`replace` semantics; the whole `/api/auth/tokens*` surface is session-only.
Round 3 (plan recon): auth tables owned by `AuthStore.init()` via `CREATE TABLE IF NOT EXISTS`
(the sibling-store convention), **not** the history `user_version` migration chain — simpler and
still fresh-DB-safe (§3).*

## 1. Goal & context

Replace the single **shared bearer token** with a real **username/password** auth system —
"like Home Assistant" — supporting **multiple users with three roles**. This is the pre-commercial
trust gate: a product that touches a stranger's battery and (eventually) is reachable off-LAN
cannot ship on one shared secret.

**North-star constraints (unchanged):** local-first (no cloud in the auth/control path), fail-safe
(auth changes never touch the headless control loop), KISS, and the two hard technical invariants
below.

### Roles & permission model (confirmed)
- **reader** — view only, plus manage their own account (password, own read-only tokens). No
  settings, no control, no chat.
- **user** — everything *except* user management: change all settings, manual battery override,
  **arm/disarm live control** (`control.operational`), manage their own account/tokens.
- **admin** — everything, **including** user management (invite/create/remove users, assign roles).

Three permission **tiers** map the API surface (not a per-route matrix). A token always inherits
its owner's role, so a reader's minted token is read-only:

| Tier | Example paths | reader | user | admin |
|---|---|---|---|---|
| `VIEW` + self-account | all `/api/` reads; `/api/auth/me`, `/api/auth/logout`, `/api/auth/password`, `/api/auth/tokens*` (own) | ✓ | ✓ | ✓ |
| `OPERATE` | `/api/override`, `/api/settings`, `/api/car/soc`, `/api/ai/validate`, `/api/chat`, `/api/notifications/read` | ✗ | ✓ | ✓ |
| `ADMIN` | `/api/users*`, `/api/invites*` | ✗ | ✗ | ✓ |

**401** = not authenticated; **403** = authenticated but role too low. Self-account writes
(change-own-password, logout, mint-own-token) require a valid session but only the `VIEW` tier.

### Key decisions (from brainstorming)
- **Session/credential model:** **opaque bearer tokens** (Approach A). A login yields a random
  server-side token; sessions and long-lived machine tokens are the same shape. Not JWT (revocation
  would need a blocklist anyway; no benefit at single-home scale), not OAuth (that would put a cloud
  IdP in the auth path — against local-first).
- **Provisioning:** **one-time invite links** — admin generates a local invite (with a role); the
  invitee opens it and sets their own username + password. No email/SMTP.
- **Machine access:** **long-lived access tokens** (HA-style), minted per user, revocable. The iOS
  **widget** rides one. The existing shared token migrates into one on upgrade.
- **iOS:** full username/password **login** (not just a pasted token).
- **Existing-install migration:** **forced onboarding on launch** — the web UI serves only
  "create the first admin" until done; the headless control loop is untouched throughout.
- **Password hashing:** **Argon2id** via `argon2-cffi` (one new dependency; arm64/Apple-Silicon
  wheels exist).

## 2. Hard invariants to preserve

1. **The gate stays pure-ASGI.** `_AccessMiddleware` (`ems/web/api.py` ~L1300, wired at ~L1344)
   must remain a pure-ASGI middleware, **not** `@app.middleware`/`BaseHTTPMiddleware` — the latter
   wraps each request in an anyio task group that starves the override endpoint's
   `asyncio.create_task` control cycle. (The existing override-cycle test guards this; it is flaky
   in isolation, green in the full suite.)
2. **CSRF stays origin-header based.** `_cross_origin_write()` runs first, independent of the token,
   and there are **no cookies** — so this design adds **no new CSRF surface**. (This is a core
   reason opaque bearer tokens beat cookies here: the app is often served over plain http on the
   LAN, where `Secure` cookies won't set.)

## 3. Data model (new migration → schema v4)

`AuthStore` at `ems/storage/auth.py` **owns its own schema**, exactly like the other sibling stores
on the shared SQLite file (`SettingsStore`, `AuditStore`, `CacheStore`): its `init()` runs
`CREATE TABLE IF NOT EXISTS` for all three tables (+ indexes) on every startup, via module-level
DDL constants. Because this runs **unconditionally** — fresh *or* existing DB — the finding-2
failure mode (a stamped fresh DB missing its tables) **cannot occur**: there is no fresh-vs-migration
split to get wrong.

The auth tables are deliberately **not** part of the `HistoryStore` `user_version` migration chain.
That chain versions the *history* schema only, and `has_table(db, "raw_samples")` keys the
"existing schema" decision off `raw_samples` specifically, so sibling stores' tables never confuse
migration detection (`migrations.py:62-70`). This is the established pattern — auth follows it.

**Startup ordering:** `AuthStore` is constructed in `main.py:build_app()` and
`await auth_store.init()` is added to the `create_app` lifespan (`api.py` ~L1138, beside the other
stores' inits), so the tables exist before the app serves its first request (the `TestClient`
context manager runs this lifespan too).

```
users
  id            INTEGER PRIMARY KEY
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE
  password_hash TEXT NOT NULL                       -- Argon2id encoded (salt+params inline)
  role          TEXT NOT NULL CHECK(role IN ('reader','user','admin'))
  disabled      INTEGER NOT NULL DEFAULT 0          -- soft-disable; never hard-delete
  created_at    TEXT NOT NULL
  last_login_at TEXT

auth_tokens                                          -- sessions AND machine tokens, one table
  id           INTEGER PRIMARY KEY
  user_id      INTEGER NOT NULL REFERENCES users(id)
  token_hash   TEXT NOT NULL UNIQUE                 -- sha256(raw); raw is never stored
  kind         TEXT NOT NULL CHECK(kind IN ('session','access'))
  name         TEXT                                 -- access tokens: "iOS widget"; null for sessions
  created_at   TEXT NOT NULL
  last_used_at TEXT
  expires_at   TEXT                                 -- sessions: TTL; access: null (revocable)

invites
  id          INTEGER PRIMARY KEY
  token_hash  TEXT NOT NULL UNIQUE                  -- sha256(raw invite code)
  role        TEXT NOT NULL CHECK(role IN ('reader','user','admin'))
  created_by  INTEGER REFERENCES users(id)
  created_at  TEXT NOT NULL
  expires_at  TEXT NOT NULL                         -- e.g. +7 days
  used_at     TEXT                                  -- single-use; null until consumed
```

**Token cryptography:** raw token = `secrets.token_urlsafe(32)` (256-bit). Store **only
`sha256(raw)`**; look up by hash. SHA-256 (not Argon2) is correct for tokens because they are
high-entropy random values; Argon2id is used only for the low-entropy passwords in `users`.

## 4. Middleware & authorization change

`_AccessMiddleware` keeps its shape. The **origin/CSRF gate is unchanged and still runs first.**
Only the token gate's body changes:

- **Before:** `secrets.compare_digest(presented, single_shared_token)`.
- **After:** `principal = AuthStore.resolve(presented)` → `{user_id, role, token_id, kind}` or
  `None` (token exists, not expired, user not disabled). One indexed lookup.

The principal carries **`token_id` and `kind`** (`'session'` | `'access'`), not just
`{user_id, role}`: some self-account writes are restricted to interactive sessions (§5), and
`token_id` is needed for owner-scoped token operations (§5).

**Two freshness fields, deliberately different reliability:**
- `last_used_at` — telemetry only, updated **best-effort** (non-blocking; a dropped write is
  harmless).
- `expires_at` (sessions only) — auth-critical. Refreshed **synchronously**, but **only when within
  a 7-day window of expiry** (of the ~30-day TTL). This bounds write frequency *and* guarantees an
  active user is never logged out by a dropped best-effort write. Access tokens have no sliding
  expiry.

A new **`ems/web/authz.py`** holds the declarative path→tier map and `role_satisfies(role, tier)`.
The existing invariant test `test_every_mutating_route_is_write_gated_or_explicitly_exempt`
**generalizes** to *"every route declares a permission tier"* — so no route can silently ship
ungated. `_WRITE_API_PATHS` / `WRITE_EXEMPT_PATHS` fold into this map.

**Auth-exempt paths** (reachable logged-out; extends `_AUTH_EXEMPT_API_PATHS`): `/api/auth`
(discovery), `/api/auth/login`, `/api/auth/onboard`, `/api/invites/accept`, `/health`, and the SPA
shell/static (the middleware still only inspects `/api/`, so these are untouched).

`AppContext` (`ems/web/context.py`) gains the `AuthStore` handle + the auth helpers (today
`_authorized`/`_effective_web_token` live inline in `api.py`) so routers in `routes/` can reach them.

## 5. API surface

Two new `build_router(ctx) -> APIRouter` modules under `ems/web/routes/`, included in the loop at
`api.py` ~L3443:

**`routes/auth.py`**
- `GET  /api/auth` *(exempt)* → `{required, authenticated, onboarding_needed, user?}` (extends today's endpoint)
- `POST /api/auth/login` *(exempt)* `{username, password}` → `{token, user:{username, role}}` (rate-limited)
- `POST /api/auth/onboard` *(exempt)* `{username, password[, shared_token]}` → creates first admin + logs in; **409** if any user exists; requires the shared token when one is configured (anti-seizure). Guarded by one `BEGIN IMMEDIATE` transaction that rechecks `COUNT(users)=0` inside (§6, no double-admin race)
- `POST /api/auth/logout` → revoke the **current session** — `kind=='session'` only (access tokens are revoked via `DELETE /api/auth/tokens/{id}`)
- `GET  /api/auth/me` → current principal — accepts **any** valid token (session or access; read-only self info)
- `POST /api/auth/password` `{old, new}` → change own password — **`kind=='session'` required**; **403** for an access token (a leaked machine/widget token must not change the password)
- `POST /api/auth/tokens` `{name, replace?: bool}` → mint a long-lived `access` token; raw shown once. With `replace: true` it does an **atomic revoke-and-remint by (owner, name)** — deletes the caller's tokens of that name and inserts the replacement **in one transaction** (the contract the iOS widget relies on, §7). **`kind=='session'` required** (a machine token cannot mint/replace credentials)
- `GET/DELETE /api/auth/tokens[/{id}]` → list/revoke — **owner-scoped** (`WHERE id=? AND user_id=?`, so path-tier auth can't be used for an IDOR) **and `kind=='session'` required**. The whole `/api/auth/tokens*` surface is interactive-session-only: a machine token is for API access, not for managing the account's credential set. (Orthogonal to the tier: any *role* including reader may manage its **own** tokens, but only from a session.)

**`routes/users.py`** (`ADMIN` tier)
- `GET /api/users` · `PATCH /api/users/{id}` (role/disable) · `DELETE /api/users/{id}` (soft-disable + revoke tokens)
- `POST /api/invites` `{role}` → `{accept_url, expires_at}` · `GET /api/invites` · `DELETE /api/invites/{id}`
- `POST /api/invites/accept` *(exempt)* `{code, username, password}` → create user with the invite's role, consume the invite, log in

**Guards (all transactional — see §6):** cannot remove or demote the **last admin**, and cannot
remove/demote **yourself** out of admin.

## 6. Core flows

- **Login:** verify Argon2id hash → create `session` token → return raw token. Generic "invalid
  credentials" + a dummy hash when the user is missing → **no username enumeration**.
- **Invite:** admin `POST /api/invites {role}` → raw code in an accept URL → admin copies it to the
  person → `POST /api/invites/accept` validates (exists, unexpired, unused), creates the user, marks
  the invite used, logs them in.
- **Machine token:** user mints a named `access` token (shown once), revocable any time.
- **Audit:** every auth event (login ok/fail, onboarding, user/role/token/invite create/delete) →
  the existing audit log.

**Concurrency — every guard is enforced inside one write transaction with the condition rechecked
inside (`BEGIN IMMEDIATE`), never read-then-write** (finding 4, TOCTOU):
- **Onboarding:** one transaction rechecks `COUNT(users)=0`, inserts the admin, creates the admin's
  session, **and** — when a shared token is configured — inserts the migrated access token, **all
  atomic**. A crash can't leave onboarding closed (users exist) while the shared-token migration is
  undone, which would strand the widget/scripts with no path to re-run it (finding). Two
  simultaneous requests still can't both create an initial admin.
- **Invite accept:** atomic single-use consume — `UPDATE invites SET used_at=? WHERE id=? AND
  used_at IS NULL`, require `rowcount==1`, and create the user in the **same** transaction. No
  double-consumption.
- **Last-admin guard:** recheck that ≥1 *other* enabled admin remains before demote/disable/delete.
  Two concurrent demotes can't leave zero admins.
- Username uniqueness is additionally enforced by the `UNIQUE COLLATE NOCASE` constraint.

## 7. Clients

### Web (`ems/web/frontend/`)
The token *shape* is unchanged, so `auth.ts` + `authHeaders()` and all existing `fetch` sites keep
working — screens are added on top, fetches are not rewired.
- **Login gate**, **onboarding** screen (shown on `onboarding_needed`), **invite-accept** route
  (`/#/accept-invite?code=…`).
- **Admin UI** under Settings → "Access & security": user list (role change, disable/remove),
  create-invite (copy link), pending invites (revoke), token mint/list/revoke (raw shown once),
  logout.
- **Reader = read-only UI:** OPERATE controls hidden/disabled for readers (mirrors the API 403).
- **Global 401 handler:** clears the token, bounces to login.
- The old **"paste a token" Access box is retired** (machine tokens are minted in-UI).

### iOS (`ios/EMSControl/`, in scope)
- `ConnectionView` token field → **username/password login** → `POST /api/auth/login` → store the
  session token in Keychain (same `APIClient.token` plumbing below the login screen).
- **Widget token — revoke-and-remint per device, not "idempotent"** (finding 3): access-token raw
  values are shown **only at mint**, so the server can't hand back an existing token — reinstall,
  cleared app-group defaults, or a second device could never recover one. On login the app calls
  `POST /api/auth/tokens {name: "iOS widget · <device>", replace: true}` — a **single atomic
  revoke-and-remint** by name (§5) — persists the raw value locally (Keychain + app-group), and
  mirrors it to the widget. Each device gets its own independently revocable token; losing the local
  copy just re-mints on next login.
- **Replace** the current behavior where saving a server overwrites the widget config with the
  interactive token (`ios/EMSControl/Sources/EMSControlCore/DashboardStore.swift:65`) — the widget
  must carry the dedicated access token, never the session token.
- New Swift files require `xcodegen generate` (the xcodebuild app target won't see them otherwise).

## 8. Migration & backward-compatibility (forced onboarding)

- Migration **v4** creates the tables. **While zero users exist**, the middleware serves only the
  exempt onboarding/login/static paths → the SPA shows "create your admin account." **The headless
  control loop is unaffected** (it does not pass through the web gate).
- **Anti-seizure:** if a shared token (`EMS_WEB_TOKEN` env or `web.auth_token` setting) is
  configured, `/api/auth/onboard` **requires it** — a stranger on the LAN cannot grab admin during
  the upgrade window. Fresh installs (no prior token) onboard first-come (like Home Assistant).
- **Shared-token conversion is part of the onboarding transaction** (not a later step): the shared
  token is inserted as an `access` token owned by the new admin ("Migrated shared token") in the
  **same** `BEGIN IMMEDIATE` that creates the admin (§6), so the widget/scripts revive atomically —
  a crash cannot close onboarding while leaving the migration undone. Legacy shared-token auth is
  then disabled; `web.auth_token` is deprecated and `require_auth` becomes implicitly always-on
  (fulfils B-83).

## 9. Security hardening

- **Argon2id** params tuned for the Pi target (bounded memory/time cost). Passwords never logged or
  exported.
- **Login rate-limiting:** per-username backoff + lockout after N failures in a window (in-memory;
  single process is sufficient).
- **Session TTL** ~30 days; `expires_at` refreshed synchronously **only within 7 days of expiry**
  (§4), so a best-effort telemetry write can never log an active user out. Access tokens: no expiry,
  revocable.
- **Strict CSP** tightened to self-only — the primary mitigation for a localStorage-held token, and
  cheap because everything is bundled with no runtime CDN.
- **Export/redaction:** `users`/`auth_tokens`/`invites` are **never** included in the diagnostics
  bundle; extend the existing leak-prevention denylist test (`test_export_package.py`).
- **Transport:** remote-over-internet still requires HTTPS/VPN — documented; delivered properly by
  B-39.

## 10. Testing

- **Unit:** AuthStore (hash/verify, token lookup + expiry, single-use invites, last-admin guard).
- **Kind enforcement (finding 1):** an `access` token gets **403** on change-password and every
  `/api/auth/tokens*` operation (list/mint/revoke); **200** on `/api/auth/me`.
- **Fresh-DB schema (finding 2):** a brand-new DB reports schema v4 **and** has all three auth
  tables — guards the baseline-vs-migration split.
- **Concurrency (finding 4):** parallel onboarding → exactly one admin; parallel invite-accept →
  consumed once; parallel last-admin demote → ≥1 admin always remains.
- **Atomic onboarding+migration:** onboarding commits admin + session + migrated shared-token as a
  unit; a fault between them rolls back all — never a "closed onboarding, unmigrated token" state.
- **Owner-scoping (finding 6):** user B cannot list, read, or `DELETE` user A's token id.
- **Session refresh (finding 5):** `expires_at` extends only inside the 7-day window; a dropped
  `last_used_at` write does not shorten a session.
- **Authz:** the generalized "every route declares a tier" invariant; reader→403, unauth→401,
  admin-only coverage.
- **Middleware:** the pure-ASGI override-cycle test stays green (flaky solo, green in the full
  suite); origin-CSRF behaviour unchanged.
- **Migration:** v4 applies on an existing DB; shared-token→admin conversion; forced-onboarding
  gating (zero-users state).
- **Anti-abuse:** rate-limit/lockout; timing equalization (no user enumeration).
- **Widget token (finding 3):** `POST /api/auth/tokens {name, replace:true}` atomically replaces the
  prior same-name token (old hash invalid, new works) in one call; re-login after a cleared
  app-group re-mints a working per-device token; a second device gets its own.
- **e2e (Playwright):** onboarding, login/logout, invite-accept, reader read-only, user operate,
  admin manage — extend `e2e/auth.spec.ts`. Requires a clean DB (known e2e constraint — repoint
  `db_path`).
- **Export leak test** extended to the new tables.
- **iOS:** login-flow test; `xcodegen generate` for new files.

## 11. Implementation slices (for the plan)

1. **Backend core** — `AuthStore` + migration v4, Argon2id, middleware identity resolution + `authz.py`
   tiers, `routes/auth.py` (login/logout/me/onboard/discovery), forced-onboarding + shared-token
   migration. Web onboarding + login gate.
2. **Users & invites** — `routes/users.py`, invite create/accept/revoke, admin user-management UI,
   reader read-only UI.
3. **Long-lived tokens + iOS** — token mint/list/revoke UI, iOS login, widget token provisioning.
4. **Hardening** — rate-limiting, CSP tightening, audit wiring, export redaction, full e2e.

Each slice is independently shippable once slice 1 makes auth real.

## 12. Out of scope (explicitly)

- OAuth / social login ("sign in with Google") — a possible *future* cloud-tier nicety, never a core
  dependency.
- Email/SMTP anything (invites are copy-the-link).
- Per-user or finer-than-tier permissions; SSO; 2FA/TOTP (candidate for a later hardening pass).
- The B-39 cloud relay itself (this design only makes it *safe to build*).
