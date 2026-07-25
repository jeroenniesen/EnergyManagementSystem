# Batch — "Apply & restart" + auth-screen styling

**Status:** design v3 (approach approved 2026-07-25: **refuse-when-busy** self-restart) · **Author:** Fable 5 · **Cross-family spec review:** Sol — v1/v2 CHANGES-REQUIRED (restart safety), v3 pending · **Base:** `main` @ `f6791ae` · **Branch:** `feat/apply-restart-auth-styling`

Two homeowner-facing fixes from the audit, batched. Built in 3 iterations + 3 polishing rounds under the multi-model rules (author self-review → Sol cross-family review that runs the tests).

**Design history (human-owned deviations, recorded):**
- v1 → Sol found the naive self-SIGTERM unsafe: an in-flight worker-thread battery write can land after the shutdown AUTO restore; unconfirmed writes aren't restored.
- v2 tried an in-process *force-quiesce*; Sol found that requires joining a non-killable `to_thread` write worker and a new cluster-wide "confirmed AUTO" read — a real control-loop concurrency project.
- **v3 (approved): refuse-when-busy.** The restart proceeds ONLY when the controller is already idle and the last battery command is confirmed-AUTO; otherwise it refuses (409). This is safe **by construction** — there is no in-flight writer to race and no forced mid-write quiescence — and keeps the batch right-sized. The pre-existing general shutdown race (present on every SIGTERM today) is **out of scope**, recorded as a separate tracked safety item (§Discovered).

## Item A — Style the auth screens

**Problem.** `Login`/`Onboarding`/`AcceptInvite` render *before* the styled shell (`App.tsx:640-664`, above the final `<SkyBackdrop/> + <div className="app">`), and `styles.css` has zero selectors for them — the first thing a homeowner sees is unstyled browser inputs.

**Design.** A shared **`AuthLayout`** centers a card reusing existing tokens (`--panel/--line/--radius/--shadow/--accent`, light+dark), the panel recipe (`styles.css:421-431`/`691-698`), and the `.field` form pattern (`styles.css:1259-1303`). No new visual language.
- `AuthLayout.tsx`: `<AuthLayout title=… testid?>{children}</AuthLayout>` — **AuthLayout owns the heading** (renders `<h1>`; the screens drop their bare `<h1>` and pass `title`). Full-viewport centered container behind `<SkyBackdrop/>`.
- CSS (appended, theme-aware): `.auth-screen` (fixed, flex-center, `min-height:100dvh`, padding, **`overflow-y:auto`** so landscape / open mobile keyboard never clips), `.auth-card` (panel recipe, `width:100%; max-width:420px; padding:clamp(20px,5vw,32px)`), `.auth-card h1`, full-width submit, error via existing `.field-err`.
- **All text inputs explicit `type="text"`** (Username, Existing-access-token) — `.field input` CSS only matches `[type="text"]`/`[type="password"]` (`styles.css:1266`).
- **Preserve (e2e contract):** outer `data-testid` (`login`/`onboarding`/`accept-invite`), field `aria-label`s (`Username`/`Password`/`Existing access token`), submit button text (`Sign in`/`Create admin`/`Create account`). Add `data-testid="auth-card"`.

**Files:** create `AuthLayout.tsx`; modify `Login.tsx`/`Onboarding.tsx`/`AcceptInvite.tsx`/`styles.css`. Bundle ≤ 300 KB gz.

## Item B — Make "restart to apply" real (refuse-when-busy self-restart)

**Problem (no-op, confirmed).** 12 `applies="restart"` settings are read once at boot (`main.py:34-57` → `build_wiring`); saving them updates the cache/DB + a UI flag but the live objects keep old values until the process restarts, and nothing restarts it.

### B.0 The safety rule — restart only when idle-and-safe (by construction)
The restart NEVER forces the battery or waits on a live write. It proceeds only when the controller is provably quiescent, else it refuses. `POST /api/system/restart` computes **idle-and-safe** as ALL of:
1. **No restart already in flight** — a synchronous check-and-set of `app.state._restart_requested` (set BEFORE the first `await`); a second concurrent request → 409.
2. **No control write in flight** — I2 adds a lightweight `write_in_flight` guard (set immediately before `driver.apply()` / the mode write and cleared in a `finally`, so it is true even while a timed-out `to_thread` worker is still inside the driver). Idle requires `write_in_flight == False`.
3. **No control cycle admitted** — `control_lock` is immediately acquirable (non-blocking `try`); if a cycle holds it, busy. (Combined with (2), this covers both the awaited cycle and a detached-but-still-writing worker.)
4. **No override cycle task pending** — the tracked override tasks (`service.py:327`, `api.py:2266/2292`) are all done.
5. **Battery is confirmed-AUTO or was never commanded** — `controller.last_confirmed_action in {AUTO, None}` (the existing SetData-acceptance confirmation model — `mode_controller.py:380`; no new physical cluster re-read). Dry-run / not-operational always satisfies this (the driver is never armed).

If any of (2)-(5) fails → **409** `{"detail":"controller is mid-operation — try again in a few seconds","reason":<which>}` and the endpoint releases `_restart_requested`. If all pass, the endpoint holds `_restart_requested` (blocking new admissions is not required beyond this because a fresh cycle can't complete a write before SIGTERM; but the handler sets a `draining` flag too, checked at the top of `run_cycle`/override paths, as belt-and-suspenders so no new write even starts in the sub-second window before exit) and restarts (B.1).

Because the battery is already confirmed-AUTO with no writer running, the subsequent shutdown is safe with the EXISTING `_shutdown_restore` untouched (it sees AUTO and no-ops). No worker-join, no forced quiesce, no deadline race.

### B.1 Backend endpoint `POST /api/system/restart`
- **Registered only when `auth_store` is wired** (identity system present). In legacy `create_app(auth_store=None)` mode the route is **not registered** (the legacy middleware branch bypasses `required_tier`/`requires_session` — `api.py:1471`). The handler also asserts a resolved `auth_principal` and **fails closed (403)** otherwise.
- **Gating: ADMIN + session-only.** Add `/api/system/restart` to `authz.ADMIN_PATHS` (→ ADMIN) AND `_SESSION_ONLY_PATHS` (→ requires_session) — verified: `authz.py:86/94` + middleware `api.py:1500`. No machine/access token can restart.
- **Supervised guard, strict parsing.** Shared `_is_supervised()` reads `EMS_SUPERVISED` via an explicit truthy allow-list `{"1","true","yes","on"}` (case-insensitive) — NOT `bool(os.getenv(...))`. Set `EMS_SUPERVISED=1` in the launchd plist `EnvironmentVariables` (`scripts/install.sh`, single-uvicorn + `KeepAlive`) and `docker-compose*.yml` (single-uvicorn + `restart:unless-stopped`). Unsupervised → **409** `{"detail":"not supervised; restart manually via scripts/restart.sh"}`, no exit.
- **Order of checks (fail cheap first):** supervised → single-flight set → idle-and-safe (§B.0.2-5). On the first fully-valid request: (1) audit `system_restart` (actor username/id, ts) — via the auth-audit helper, which writes a category=`auth` row with `detail.event="system_restart"` (`api.py:845`); (2) return **202** `{"restarting":true,"boot_id":<current>}`; (3) AFTER the body is sent, run the restart action via an injectable `app.state.request_restart` (default = `os.kill(os.getpid(), signal.SIGTERM)`), scheduled as a **response-attached background task** (Starlette sends the body before awaiting the task — `starlette/responses.py:163`), NOT a bare sleep. If that action raises, clear `_restart_requested`+`draining` and audit the failure (process stays alive rather than wedged).
- **MUST NOT `os._exit()`** — SIGTERM → uvicorn 0.49.0 graceful shutdown (`should_exit`, then lifespan shutdown — `server.py:299/341`), so the lifespan `finally` (battery already AUTO) runs. Hard-kill would skip it.

### B.2 Boot identity (restart proof)
- At startup set `app.state.boot_id = uuid4().hex`; expose on `/health/live` (`api.py:1921`) as `boot_id`. Client records the 202's `boot_id`, polls `/health/live` until a **different** `boot_id` (proves the NEW process answered — a plain "healthy" can be the old process), then reloads.

### B.3 Server-computed capability
- Server-derived `restart_available: {available: bool, reason: str}` and `restart_pending: bool` (no client privilege inference):
  - `available` = supervised AND caller is an ADMIN **session**.
  - `restart_pending` = current values of the 12 restart-tagged settings differ from a **boot fingerprint** captured **after persisted settings load** in lifespan (`api.py:1243`), NOT from `create_app`'s initial default `settings_cache` (`api.py:895`) — else it would compare against defaults. Robust across Settings unmount/reload/discard (unlike the component-local `Settings.tsx:405` set).
  - Surface on `/api/auth/me` (already returns `kind` — `routes/auth.py:182`) or the settings read the page makes; implementer picks the least-invasive existing endpoint and documents it. (`/api/auth` discovery exposes `role` but not `kind` — `api.py:1966` — insufficient alone.)

### B.4 Frontend UX
- Show **"Apply & restart"** only when `restart_available.available && restart_pending`; if changes are pending but unsupervised, show the **manual-restart hint** (`scripts/restart.sh`). Admin-session only (server also enforces).
- Click → **confirmation** (guided-confirm pattern): *"This restarts the EMS to apply the changes. It only restarts when the battery is already in its safe AUTO mode; if the system is mid-adjustment it'll ask you to try again in a moment. The app comes back on its own in a few seconds."* → `POST /api/system/restart`.
- **202** → "Restarting…" → poll `/health/live` for a **changed `boot_id`** (timeout + "reload manually" fallback) → reload.
- **409** (busy) → friendly "The system is mid-adjustment — try again in a few seconds" toast, button stays; (unsupervised 409 shouldn't occur since the button is hidden, but handle it as the manual hint).

## Testing
Backend:
- **Gating:** 403 non-admin session; 403 admin **access token** (session-only); 409 unsupervised; 409 second concurrent (single-flight); route **absent** in `auth_store=None` mode.
- **Idle-and-safe (the safety core):** 409 with `reason` when `write_in_flight` is true / `control_lock` held / an override task pending / `last_confirmed_action` is a non-AUTO mode; **202 + `request_restart` spy invoked + audit row** only when all idle-and-safe conditions hold (incl. the dry-run/not-operational case where the driver is unarmed → always AUTO-safe). Prove the idle checks are read synchronously before the first `await` (single-flight can't double-fire).
- **`_is_supervised()`:** `"1"/"true"/"yes"/"on"` (any case) → True; `"0"/"false"/""/unset/"maybe"` → False.
- **Real uvicorn subprocess test (Sol #7):** launch a harmless instrumented app under the pinned uvicorn (subprocess), POST restart, assert the 202 is delivered, lifespan cleanup ran, and the process exits — response-before-exit ordering + lifespan execution, not just that `os.kill` was requested.
- Regression: `POST /api/settings` still returns `restart_required`; the `write_in_flight` guard doesn't change control behavior when no restart is requested.

Frontend (Playwright): auth-screen specs stay green (testids/labels/button-text unchanged) + `auth-card` visible/centered; Apply & restart with mocked `restart_available`/`restart_pending`/202/boot_id + `/health/live` boot_id change → button → confirm → "Restarting…" → reload; 409-busy → retry toast, button stays; unsupervised → manual hint, no button.

## Iteration plan (3 + 3)
- **I1 — Auth styling** (Sonnet → Sol): `AuthLayout` + CSS + wire 3 screens + explicit `type="text"` + e2e + `auth-card` assertion.
- **I2 — Restart backend** (**Opus** → Sol; safety-relevant but bounded): the `write_in_flight` guard + `draining` flag + idle-and-safe computation; the endpoint (register-only-with-auth, ADMIN+session, strict `_is_supervised`, single-flight, response-first injectable SIGTERM trigger, no `os._exit`); `boot_id`; server `restart_available`/`restart_pending` (post-load fingerprint); install.sh/compose env; backend tests incl. the uvicorn subprocess test.
- **I3 — Restart frontend UX** (Sonnet → Sol): capability-driven button + manual hint + confirm + boot_id poll + reload + 409-retry + e2e.
- **P1** — cross-cutting review sweep (Sol) + fix wave.
- **P2** — adversarial **safety** review of the restart path (Fable deep-check + `/panel` senior-software-engineer + qa-lead): prove idle-and-safe cannot pass while any writer is live or last-action non-AUTO; gating unbypassable incl. legacy; supervised-guard + boot_id sound; response-first trigger fires after body send; failure-of-trigger doesn't wedge.
- **P3** — final whole-branch review (`/panel` pre-merge) + full verify + PR.

## Non-goals
- Force-quiescing a mid-write controller / no-downtime hot-reload — out of scope by the v3 decision.
- Redesigning auth-screen content or Settings IA; ML wiring.

## Discovered pre-existing issue (recorded — separate tracked item, NOT fixed here)
Sol's review established that the CURRENT shutdown restore (`_shutdown_restore`, any SIGTERM today: launchd stop, deploy, crash-relaunch) has the same writer-quiescence race and unconfirmed-write gap the refuse-when-busy button now sidesteps for itself. This is a real, pre-existing safety gap independent of this batch. **Action:** file a follow-up hardening item (proper shutdown quiescence: join the `to_thread` write worker, cover unconfirmed/unknown state, define cluster-confirmed-AUTO). Recorded here so it isn't lost; explicitly not undertaken in this batch.

## Rollout
Additive. Restart endpoint inert unless `EMS_SUPERVISED` truthy and absent in legacy no-auth mode; refuses safely when busy. Auth styling is CSS + a wrapper — no contract change. Ships on `feat/apply-restart-auth-styling`; PR into `main`.
