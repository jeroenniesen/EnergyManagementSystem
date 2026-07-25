# Batch — "Apply & restart" + auth-screen styling

**Status:** design (fork approved 2026-07-25: self-restart button) · **Author:** Fable 5 · **Cross-family spec review:** Sol (pending) · **Base:** `main` @ `f6791ae` · **Branch:** `feat/apply-restart-auth-styling`

Two independent homeowner-facing fixes surfaced by the app audit, batched because both are small, frontend-heavy, and touch the Settings/first-run surfaces. Built in 3 iterations + 3 polishing rounds under the multi-model working rules (every artifact: author self-review → Sol cross-family review that runs the tests).

## Item A — Style the auth screens

**Problem.** `Login`, `Onboarding`, and `AcceptInvite` render *before* the app's styled shell (`App.tsx:645-664`: they return above the final `<SkyBackdrop/> + <div className="app">` return), and `styles.css` has **zero** selectors for them (no `.login`/`.onboarding`/`.accept-invite`, no bare `form`/`input`/`h1`). So the very first thing a homeowner sees — creating the admin account, and every later sign-in — is a plain top-left stack of unstyled browser inputs. Only `.btn-primary` picks up styling.

**Design.** A single shared **`AuthLayout`** component wraps all three screens in a centered card that mirrors the existing design system — no new visual language, just applying the tokens already in `styles.css`.

- **New component** `ems/web/frontend/src/AuthLayout.tsx`: `<AuthLayout title testid>{children}</AuthLayout>` renders a full-viewport centered backdrop + a `max-width: 420px` card. The three screens keep their own `<form data-testid=…>`, fields, error `<p role="alert">`, and submit button, but drop their bare `<h1>` in favor of the layout's title (or keep it, styled). The `<SkyBackdrop/>` renders behind the card so the auth screens share the app's atmosphere.
- **New CSS** (append to `styles.css`, theme-aware via the existing `:root` / `:root[data-theme="light"]` tokens — no hard-coded colors):
  - `.auth-screen` — fixed full-viewport flex-center container (mirrors `.modal-backdrop` at `styles.css:421-426`), `padding` so it never clips on mobile, `min-height: 100dvh`.
  - `.auth-card` — the panel recipe used app-wide (`background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); padding: clamp(20px, 5vw, 32px)`), `width: 100%; max-width: 420px`.
  - `.auth-card h1` — the product title/heading (size ~1.4rem, `color: var(--text)`), plus an optional muted subtitle (`color: var(--muted)`).
  - Reuse the **existing** `.field` form pattern (`styles.css:1259-1303`) for label+input+help+error rather than inventing new field CSS: the screens' inputs get wrapped so they pick up `.field input` styling (`background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px` + focus ring). Error `<p role="alert">` gets the existing `.field-err` (or `.error`) class.
  - Submit button spans full width on these screens (`.auth-card .btn-primary { width: 100%; }`).
- **Contract to preserve (e2e-critical, do NOT change):** the outer `data-testid` on each form (`login` / `onboarding` / `accept-invite`), the field `aria-label`s (`Username`, `Password`, `Existing access token`), and the submit button visible text (`Sign in` / `Create admin` / `Create account`). `auth.spec.ts` keys only off these. Styling is additive.

**Files:** create `AuthLayout.tsx`; modify `Login.tsx`, `Onboarding.tsx`, `AcceptInvite.tsx` (wrap in `AuthLayout`, apply `.field` structure), `styles.css` (append auth section). Bundle stays ≤ 300 KB gz.

## Item B — Make "restart to apply" real (self-restart button)

**Problem (confirmed no-op).** 12 of 76 settings are `applies="restart"` (device IPs, `prices.tibber_token`, `battery.indevolt_*`, `control.operational`, `reporting.carbon_signal`/`electricitymaps_api_key`). These are read **once** at boot in `build_app()` via `effective_connection()`+`build_wiring()` (`main.py:54-57`). Saving one updates `settings_cache`/DB and flips a UI `restart_required` flag, but the live source/battery-driver objects keep the **old** values until the OS process restarts — and nothing restarts it. `POST /api/settings` (`api.py:3517-3556`) computes `restart_required` only to echo it back.

**Design — an admin-gated self-restart that the supervisor relaunches.**

### B.1 Backend: `POST /api/system/restart`
- **New route** (e.g. `ems/web/routes/system.py` or fold into an existing admin router). **Gating: ADMIN + session-only.** Add `/api/system/restart` to `authz.ADMIN_PATHS` (exact path) AND to `_SESSION_ONLY_PATHS` (so no machine/access token can restart the service — same posture as user-management). The handler assumes a resolved ADMIN session principal (middleware enforces).
- **Supervised guard.** Self-restart only makes sense where a supervisor relaunches the process. Introduce `EMS_SUPERVISED` (set to `1` by `scripts/install.sh` in the launchd plist `EnvironmentVariables`, and by `docker-compose*.yml` env). If `EMS_SUPERVISED` is not truthy → the endpoint returns **409** `{"detail": "not running under a supervisor; restart manually (scripts/restart.sh)"}` and does NOT exit. This keeps a bare-uvicorn dev run from being killed with no relaunch.
- **The restart itself (safety-critical path).** The handler:
  1. audits `system_restart` (actor, ts) via `ctx.audit_auth`/audit;
  2. returns **202** `{"restarting": true}` to the client immediately;
  3. schedules a **clean** shutdown *after the response is sent* — send the process its own `SIGTERM` (`os.kill(os.getpid(), signal.SIGTERM)`) from a short `asyncio` delay / background task. Uvicorn's SIGTERM handler runs the normal graceful ASGI shutdown, so the FastAPI **lifespan `finally` still executes `_shutdown_restore()`** (battery best-effort restored to safe/AUTO, bounded 8s, audited) before the process exits. The supervisor (launchd `KeepAlive`, Docker `restart:unless-stopped`) then relaunches, and boot runs the normal **observe→validate→plan→act startup grace** (`Lifecycle`).
  - **MUST NOT** use `os._exit()`/hard-kill — that would bypass `_shutdown_restore` and leave the battery in a commanded non-AUTO mode. This is the single most important correctness constraint of the item.
- **Testability.** Do not let the test actually SIGTERM the pytest process. Inject the shutdown trigger as a small override — e.g. the handler calls `app.state.request_restart()` (default = the SIGTERM-after-delay function; tests replace it with a spy). Tests then assert: 403 for non-admin / non-session, 409 when unsupervised, 202 + `request_restart` invoked + `system_restart` audited when admin+session+supervised.

### B.2 Frontend: honest pending-state + "Apply & restart"
- The pending plumbing already exists (`restart_required` from `POST /api/settings`, `restartPending: Set<string>`, the `restart` badges + `settings-nav-restart` pill + `"· some apply on restart"` save-bar hint). Extend it:
  - When there are restart-pending changes (or on demand), show an **"Apply & restart"** button in the Settings save-bar / a small banner. Visible only to admins on a session (mirrors the existing admin-gated controls); hidden/disabled when `EMS_SUPERVISED` is not set (surface the manual-restart hint instead — the app already knows via a small `GET` field, see B.3).
  - Clicking → a **confirmation** step (reuse the existing guided-confirm pattern used for risky overrides) that states plainly: *"This restarts the EMS to apply the changes. The battery briefly returns to its safe mode for a few seconds, then the app comes back on its own."* → on confirm, `POST /api/system/restart`.
  - After 202: show a **"Restarting…"** state, then **poll `GET /health/live`** until it returns healthy again (with a timeout + "still restarting / reload manually" fallback), then reload the page. Never leave the user on a dead screen with no feedback.
- Copy stays honest (project voice): the button doesn't promise zero-downtime; it says what actually happens.

### B.3 Supervised flag to the client
- Surface whether a supervised restart is available so the UI shows the button vs. the manual-restart hint. Add `supervised: bool` (from `EMS_SUPERVISED`) to an existing read the Settings page already makes (e.g. the `/api/auth` discovery payload, or `/api/diagnostics`, or the settings GET) — pick the one Settings already consumes to avoid a new fetch. (Implementer picks the least-invasive existing endpoint; the middleware tiering for that endpoint is unchanged.)

## Testing

Backend (`ems/tests/`):
- `/api/system/restart`: 403 for a non-admin session; 403 for an admin **access token** (session-only); 409 when `EMS_SUPERVISED` unset; 202 + `request_restart` spy called + `system_restart` audit row when admin+session+supervised. The shutdown function itself (SIGTERM-after-delay) is unit-tested in isolation to confirm it does **not** call `os._exit` and goes through SIGTERM (assert the signal, mock `os.kill`).
- Regression: `POST /api/settings` still returns `restart_required` correctly; no change to the settings-apply hooks.

Frontend (Playwright, `auth.spec.ts` + `manage`/settings specs):
- Auth screens: after styling, the existing onboarding→login→logout, invite-accept, and account-token specs stay green (testids/labels/button-text unchanged); add a light assertion that the auth card is centered/visible (e.g. `account-tokens`-style testid on the card, `data-testid="auth-card"`).
- Apply & restart: with a mocked `/api/system/restart` (202) and a mocked `supervised:true`, saving a restart-tagged field surfaces the "Apply & restart" button; confirm → posts → shows "Restarting…" → (mock `/health/live` healthy) → reload. With `supervised:false`, the manual-restart hint shows instead of the button.

## Iteration plan (3 + 3)
- **I1 — Auth styling** (Sonnet author → Sol review): `AuthLayout` + CSS + wire the 3 screens + e2e stays green + card assertion.
- **I2 — Restart backend** (Opus author → Sol review): the endpoint, session+ADMIN gating, `EMS_SUPERVISED` guard, safe SIGTERM shutdown via injectable `request_restart`, `supervised` surfaced to the client, install.sh/compose env, tests. *(Safety-critical — Opus, not Sonnet.)*
- **I3 — Restart frontend UX** (Sonnet author → Sol review): pending-state + "Apply & restart" button + confirm + poll `/health/live` + reload + e2e.
- **P1** — cross-cutting review sweep (Sol) + fix wave.
- **P2** — adversarial **safety** review of the restart path (Fable deep-check + Sol): prove `_shutdown_restore` always runs (no `os._exit`), the supervised-guard can't be bypassed, no non-admin/machine-token can restart, and the battery-safe posture across the restart window holds.
- **P3** — final whole-branch review (`panel` pre-merge) + verify + PR.

## Non-goals / out of scope
- **Hot-reload** of the affected subsystems (no-downtime live re-wire) — explicitly deferred as its own larger/riskier follow-up (touches the live control loop; `control.operational` arming needs the Lifecycle observe-before-act safeguards).
- Redesigning the auth screens' *content* or the Settings information architecture — this is styling + one action, not a rework.

## Rollout
Additive. The restart endpoint is inert unless `EMS_SUPERVISED` is set (so dev/bare-uvicorn is unaffected). Auth styling is CSS + a wrapper component — no API/contract change. Ships on `feat/apply-restart-auth-styling`; PR into `main`.
