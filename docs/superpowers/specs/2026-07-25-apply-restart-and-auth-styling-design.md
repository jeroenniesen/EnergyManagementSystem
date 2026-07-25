# Batch — "Apply & restart" + auth-screen styling

**Status:** design v2 (fork approved 2026-07-25: self-restart button) · **Author:** Fable 5 · **Cross-family spec review:** Sol — v1 CHANGES-REQUIRED (safety races found), v2 pending · **Base:** `main` @ `f6791ae` · **Branch:** `feat/apply-restart-auth-styling`

Two homeowner-facing fixes from the audit, batched (both small, frontend-heavy, touch Settings/first-run). Built in 3 iterations + 3 polishing rounds under the multi-model rules (author self-review → Sol cross-family review that runs the tests).

**v2 note:** Sol's v1 review found that a naive self-SIGTERM is *not* safe — an in-flight control/override battery write (worker thread) can land after the shutdown-time AUTO restore, and unconfirmed/timed-out writes aren't restored at all. v2 makes the restart perform an explicit **quiescence + serialized safe write** *before* it exits, and reuses that mechanism to harden the normal shutdown path too.

## Item A — Style the auth screens

**Problem.** `Login`/`Onboarding`/`AcceptInvite` render *before* the styled shell (`App.tsx:640-664`, above the final `<SkyBackdrop/> + <div className="app">`), and `styles.css` has zero selectors for them — the first thing a homeowner sees is unstyled browser inputs.

**Design.** A shared **`AuthLayout`** centers a card that reuses the existing tokens (`--panel/--line/--radius/--shadow/--accent`, light+dark), the panel recipe (`styles.css:421-431`/`691-698`), and the `.field` form pattern (`styles.css:1259-1303`). No new visual language.
- `AuthLayout.tsx`: `<AuthLayout title=… testid?>{children}</AuthLayout>` — **AuthLayout owns the heading** (renders the product/screen title `<h1>`; the three screens drop their bare `<h1>` and pass a `title` prop). Full-viewport centered container behind the `<SkyBackdrop/>`.
- CSS (appended, theme-aware): `.auth-screen` (fixed, flex-center, `min-height: 100dvh`, `padding`, **`overflow-y: auto`** so small/landscape screens and an open mobile keyboard never clip), `.auth-card` (panel recipe, `width:100%; max-width:420px; padding: clamp(20px,5vw,32px)`), `.auth-card h1`, full-width submit (`.auth-card .btn-primary{width:100%}`), error via existing `.field-err`.
- **All text inputs must be explicit `type="text"`** (Username, Existing-access-token) — the existing `.field input` CSS only matches `input[type="text"]`/`[type="password"]` (`styles.css:1266`); an implicit-type input would render unstyled.
- **Preserve (e2e contract, do NOT change):** outer `data-testid` (`login`/`onboarding`/`accept-invite`), field `aria-label`s (`Username`/`Password`/`Existing access token`), submit button text (`Sign in`/`Create admin`/`Create account`). Add `data-testid="auth-card"` for a visibility assertion.

**Files:** create `AuthLayout.tsx`; modify `Login.tsx`/`Onboarding.tsx`/`AcceptInvite.tsx`/`styles.css`. Bundle ≤ 300 KB gz.

## Item B — Make "restart to apply" real (safe, quiesced self-restart)

**Problem (no-op, confirmed).** 12 `applies="restart"` settings are read once at boot (`main.py:34-57` → `build_wiring`); saving them updates `settings_cache`/DB + a UI flag but the live source/battery objects keep old values until the process restarts, and nothing restarts it.

### B.0 The safety core — quiescence + serialized safe write (NEW in v2)
The restart (and, reusing the same helper, the normal ASGI shutdown) must guarantee the battery ends in a **known safe state (AUTO)** with **no writer able to run after it**. Add to `ControlService` a `quiesce_for_shutdown(*, deadline_s)` coroutine:
1. **Stop admission.** Set a `draining` flag checked at the top of `run_cycle()` and of every override-triggered write path (`api.py:2266/2292` detached override cycles) — once set, these return WITHOUT starting a new battery write.
2. **Drain in-flight writers.** Acquire the existing `control_lock` (serializes control cycles) so the current cycle's worker-thread write has completed; and `await` the tracked override-cycle tasks (they are already tracked — `api.py:2266/2292`). Override writes must go through the same `control_lock`/drain so nothing writes outside this serialization (route them through it if they don't today).
3. **Serialized final safe write.** Still holding `control_lock` with `draining` set (no new writers possible), command **AUTO** and confirm it — UNLESS the controller can prove the battery is already confirmed-AUTO. The predicate covers **requested / unconfirmed / unknown**: if any non-AUTO mode was ever commanded or *attempted* (including a timed-out/`BatteryWriteUnconfirmed` write where `last_confirmed_action` was NOT updated — `mode_controller.py:339-341`), issue AUTO. Retry within `deadline_s`.
4. **Honest bound.** The underlying HTTP write thread cannot be force-killed (`wait_for` only times out the await — `api.py:1221`). The real bound is drain+lock (wait for the *current* write to finish) plus the driver's own per-request timeouts/retries (`indevolt_driver.py:202/269`). If AUTO cannot be confirmed within `deadline_s`, **audit a loud alert** (`battery may remain in a forced mode`) and proceed to exit anyway (never hang). This replaces the false "bounded 8s" claim with an honest, cooperative contract.

`_shutdown_restore()` (`api.py:1221`) is refactored to call `quiesce_for_shutdown()` (or becomes a thin idempotent backstop after it), so the **normal SIGTERM/deploy/stop path is hardened too**, not just the button.

### B.1 Backend endpoint `POST /api/system/restart`
- **Only registered when the identity system is wired** (`auth_store` is not None). In legacy `create_app(auth_store=None)` mode the route is **not registered** (the legacy middleware branch doesn't consult `required_tier`/`requires_session` — `api.py:1471` — so it must never be reachable there). The handler additionally asserts `request.scope.get("auth_principal")` is a resolved principal and **fails closed** (403) otherwise — belt and suspenders.
- **Gating: ADMIN + session-only.** Add `/api/system/restart` to `authz.ADMIN_PATHS` (exact path → ADMIN) AND `_SESSION_ONLY_PATHS` (→ `requires_session`), so no machine/access token can restart (verified path: `authz.py:86/94` + middleware `api.py:1500`).
- **Supervised guard, strict parsing.** A shared helper `_is_supervised()` reads `EMS_SUPERVISED` with an explicit truthy allow-list `{"1","true","yes","on"}` (case-insensitive) — NOT `bool(os.getenv(...))`. Set `EMS_SUPERVISED=1` in the launchd plist `EnvironmentVariables` (`scripts/install.sh`) and `docker-compose*.yml` env. If not supervised → **409** `{"detail":"not supervised; restart manually via scripts/restart.sh"}`, no exit.
- **Single-flight, response-first trigger.** A module/`app.state` `_restart_requested` flag makes a second concurrent request return **409** (`already restarting`). On the first valid request: (1) audit `system_restart` (actor username/id, ts) — via the audit store with an explicit event; note `ctx.audit_auth` writes a category=`auth` row with `detail.event` (`api.py:845`), so the spec's audit shape is "auth-category row, `event="system_restart"`, actor fields" (implementer confirms the exact call). (2) return **202** `{"restarting":true,"boot_id":<current>}`. (3) AFTER the response is delivered, run the restart action via an injectable `app.state.request_restart` (default: `await quiesce_for_shutdown(deadline_s=…)` then `os.kill(os.getpid(), signal.SIGTERM)`), scheduled as a response-attached background task / post-send hook — NOT a bare sleep. It must schedule successfully before the 202 is committed.
- **MUST NOT `os._exit()`** — SIGTERM routes through uvicorn's graceful shutdown (verified: uvicorn 0.49.0 SIGTERM sets `should_exit`, runs lifespan shutdown — `server.py:299/341`) so the lifespan `finally` (already-quiesced) runs. Hard-kill would bypass safety.

### B.2 Boot identity (restart proof)
- At startup set `app.state.boot_id = uuid4().hex`. Expose it on **`/health/live`** (already exists, `api.py:1921`, returns `{"status":"alive"}`) → add `"boot_id"`. The client records the pre-restart `boot_id` (from the 202) and polls `/health/live` until it returns a **different** `boot_id`, then reloads — proving the NEW process answered (a plain "healthy" poll can be answered by the still-running old process).

### B.3 Server-computed capability (no client inference)
- Expose a server-derived `restart_available: {available: bool, reason: str}` and `restart_pending: bool`, computed on the server:
  - `available` = supervised AND caller is an ADMIN **session** (server knows both; the client must NOT infer from `supervised` alone).
  - `restart_pending` = derived from a **boot-time fingerprint**: capture the values of the 12 restart-tagged settings at `build_app()` boot into `app.state.boot_restart_fingerprint`; `restart_pending` = current `settings_cache` values of those keys differ from the fingerprint. This is robust across Settings unmount / page reload / discard (unlike the component-local `restartPending` set at `Settings.tsx:405`).
  - Surface both on an endpoint Settings already consumes — extend **`/api/auth/me`** (already returns `kind` — `routes/auth.py:182`) or the settings/diagnostics read the page makes; implementer picks the least-invasive one and documents it. (The current `/api/auth` discovery payload exposes `role` but not `kind` — `api.py:1966` — so it is NOT sufficient alone.)

### B.4 Frontend UX
- Show an **"Apply & restart"** control (Settings save-bar/banner) only when `restart_available.available && restart_pending`; when supervised is false but changes are pending, show the **manual-restart hint** (`scripts/restart.sh`) instead. Admin-session only (server also enforces).
- Click → **confirmation** (reuse the guided-confirm pattern) stating plainly: *"This restarts the EMS to apply the changes. The battery is first returned to its safe AUTO mode, then the app restarts and comes back on its own in a few seconds."* → `POST /api/system/restart`.
- On 202: **"Restarting…"** → poll `/health/live` for a **changed `boot_id`** (timeout + "still restarting / reload manually" fallback) → reload. Never a dead screen.

## Testing
Backend:
- **Gating:** 403 non-admin session; 403 admin **access token** (session-only); 409 unsupervised; 409 second concurrent request; 202 + `request_restart` spy invoked + audit row when admin+session+supervised. Route **absent** in `auth_store=None` mode.
- **Quiescence (the safety core):** with a mock driver, prove `quiesce_for_shutdown` (a) sets `draining` so a subsequent `run_cycle`/override does NOT write; (b) with a simulated in-flight non-AUTO write, the FINAL battery command observed is AUTO (no write lands after it); (c) restores AUTO when the last action is unconfirmed/unknown (not just when `last_confirmed_action != AUTO`); (d) on a driver that never confirms, returns within `deadline_s` and audits the loud alert.
- **`_is_supervised()`:** `"1"/"true"/"yes"/"on"` (any case) → True; `"0"/"false"/""/unset/"maybe"` → False.
- **Real shutdown integration test (Sol blocking #7):** launch a harmless instrumented app under the pinned uvicorn (subprocess), hit `/api/system/restart`, assert the 202 is delivered, the lifespan cleanup/quiesce ran, and the process exits — i.e. response-before-exit ordering and lifespan execution, not just that `os.kill` was requested.
- Regression: `POST /api/settings` still returns `restart_required`; settings-apply hooks unchanged.

Frontend (Playwright): auth-screen specs stay green (testids/labels/button-text unchanged) + `auth-card` visible/centered; Apply & restart flow with mocked `restart_available`/`restart_pending`/202/boot_id + `/health/live` boot_id change → shows button → confirm → "Restarting…" → reload; unsupervised → manual hint, no button.

## Iteration plan (3 + 3)
- **I1 — Auth styling** (Sonnet → Sol): `AuthLayout` + CSS + wire 3 screens + explicit `type="text"` + e2e + `auth-card` assertion.
- **I2 — Restart safety core + backend** (**Opus** → Sol; safety-critical): `quiesce_for_shutdown` + drain flag + override-write serialization + AUTO-guaranteed restore covering unconfirmed/unknown + refactor `_shutdown_restore` to use it; the endpoint (register-only-with-auth, ADMIN+session, strict `_is_supervised`, single-flight, response-first injectable trigger, SIGTERM-not-`os._exit`); `boot_id`; server `restart_available`/`restart_pending` (boot fingerprint); install.sh/compose env; all backend tests incl. the uvicorn subprocess test.
- **I3 — Restart frontend UX** (Sonnet → Sol): capability-driven button + manual hint + confirm + boot_id poll + reload + e2e.
- **P1** — cross-cutting review sweep (Sol) + fix wave.
- **P2** — adversarial **safety** review of the quiesce/restart path (Fable deep-check + Sol via `/panel` senior-software-engineer + qa-lead): prove no writer runs after AUTO, unconfirmed states are restored, no `os._exit`, gating can't be bypassed (incl. legacy mode), supervised-guard sound, boot_id proof correct.
- **P3** — final whole-branch review (`/panel` pre-merge) + full verify + PR.

## Non-goals
- Hot-reload / no-downtime live re-wire (deferred, larger/riskier).
- Redesigning auth-screen content or Settings IA.
- A general controller-write-lock/threading rewrite beyond what `quiesce_for_shutdown` needs (the drain+serialize is scoped to the shutdown boundary).

## Rollout
Additive. Restart endpoint inert unless `EMS_SUPERVISED` truthy (dev/bare-uvicorn unaffected) and absent in legacy no-auth mode. The quiesce hardening also improves every normal SIGTERM/deploy stop. Auth styling is CSS + a wrapper — no contract change. Ships on `feat/apply-restart-auth-styling`; PR into `main`.

## Discovered pre-existing issue (recorded, human owns)
Sol's review revealed the *current* shutdown restore (any SIGTERM today) has the same writer-quiescence race and unconfirmed-write gap. v2 fixes it in-scope by routing shutdown through `quiesce_for_shutdown`. If the user prefers to NOT expand scope, the fallback is to make the restart endpoint **refuse (409) unless the controller is idle** (no in-flight write, confirmed-AUTO) — lighter, but leaves the general shutdown race for a separate hardening item. Decision recorded at approval.
