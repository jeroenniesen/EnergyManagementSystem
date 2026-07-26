# Apply-restart + auth-screen styling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Under the multi-model working rules, the second reviewer for each iteration is **cross-family (Codex/Sol via codex-delegate), which runs the tests itself** — not a same-family task-reviewer. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Style the unstyled auth screens, and make "restart to apply" a real, battery-safe, admin-only self-restart that only fires when the controller is idle.

**Architecture:** Item A = a shared `AuthLayout` + appended CSS reusing existing tokens. Item B = a **refuse-when-busy** `POST /api/system/restart` guarded by an idle-and-safe predicate whose core is a single **reserve-at-submission writer registry** (every battery-write path reserves a slot before spawning its worker, released only on real completion); on pass it audits, returns 202 with a `boot_id`, and self-SIGTERMs so the supervisor relaunches. Full design + rationale (5 Sol passes): `docs/superpowers/specs/2026-07-25-apply-restart-and-auth-styling-design.md`.

**Tech Stack:** Python 3.12/FastAPI (uvicorn 0.49.0, pinned), React+Vite/TSX, pytest + Playwright, launchd/Docker supervisors.

## Global Constraints (from the spec — every task inherits these)
- **Restart is safe BY CONSTRUCTION (refuse-when-busy):** it proceeds only when idle-and-safe = single-flight ∧ writer-registry empty ∧ NOT last-command-unconfirmed ∧ `last_confirmed_action ∈ {AUTO, None}`. Else **409**. No forced quiesce, no worker-join, no os._exit.
- **Registry:** one thread-safe outstanding-battery-work registry; reserve **synchronously at submission** (before `to_thread`/task spawn) for the control tick, override cycles, AND overrun-AUTO (moved onto a worker); release **only on the worker's real completion** (blocking function's `finally`), **never on a `wait_for` timeout**; single-admission on the control tick. Idle ⇔ registry empty.
- **Unconfirmed flag:** persisted; set on `BatteryWriteUnconfirmed`; cleared only on a confirmed SetData acceptance (incl. confirmed AUTO); blocks restart even when `last_confirmed_action` reads AUTO/None.
- **Gating:** ADMIN + session-only (`/api/system/restart` in both `authz.ADMIN_PATHS` and `_SESSION_ONLY_PATHS`); route registered only when `auth_store` is wired; handler fails closed (403) without a resolved principal. Add the path to the write-gating invariant.
- **Supervised guard:** `_is_supervised()` strict truthy allow-list `{"1","true","yes","on"}` (case-insensitive), NOT `bool(os.getenv)`. Unsupervised → 409, no exit. `EMS_SUPERVISED=1` set by install.sh (launchd) + docker-compose.
- **Restart proof:** `app.state.boot_id = uuid4().hex`, exposed on `/health/live`; client polls for a *changed* `boot_id`.
- **`restart_pending`** from a fingerprint of the 12 restart-tagged settings captured **after persisted settings load** (lifespan ~`api.py:1243`), not the default cache. `restart_available = supervised ∧ admin-session`, server-computed.
- **Trigger:** response-first (Starlette body sent before the attached background task); single-flight check-and-set synchronous before first `await`; a failing trigger clears the flags (no wedge).
- **Auth screens:** preserve testids (`login`/`onboarding`/`accept-invite`), aria-labels (`Username`/`Password`/`Existing access token`), button text (`Sign in`/`Create admin`/`Create account`); explicit `type="text"` on text inputs; `overflow-y:auto`. Bundle ≤ 300 KB gz. Reuse existing tokens/`.field`/panel CSS — no new visual language.

---

### Task 1 (I1) — Auth-screen styling  *(author: Sonnet · reviewer: Sol)*

**Files:**
- Create: `ems/web/frontend/src/AuthLayout.tsx`
- Modify: `ems/web/frontend/src/Login.tsx`, `Onboarding.tsx`, `AcceptInvite.tsx`, `styles.css`
- Test: `ems/web/frontend/e2e/auth.spec.ts`

**Interfaces — Produces:** `<AuthLayout title="…" testid="…">{children}</AuthLayout>` renders `.auth-screen > .auth-card` (with `data-testid="auth-card"`), owns the `<h1>` (title), and renders `<SkyBackdrop/>` behind. The three screens render their existing `<form data-testid=…>` + `.field`-wrapped inputs + `.btn-primary` submit inside it.

- [ ] **Step 1 — failing e2e.** Add to `auth.spec.ts` a test in the onboarding flow asserting the card renders: after loading a fresh DB, `await expect(page.getByTestId("onboarding")).toBeVisible()` (existing) AND `await expect(page.getByTestId("auth-card")).toBeVisible()`. Run `EMS_E2E_APP_PORT=8091 EMS_E2E_AUTH_PORT=8092 npx playwright test e2e/auth.spec.ts` → FAIL (no `auth-card`).
- [ ] **Step 2 — `AuthLayout.tsx`.** Create the component: full-viewport `.auth-screen` container + centered `.auth-card` (testid `auth-card`) + `<h1>{title}</h1>` + `{children}`, with `<SkyBackdrop compact />` behind. Import `SkyBackdrop` from wherever `App.tsx` imports it.
- [ ] **Step 3 — wire the 3 screens.** In each of `Login.tsx`/`Onboarding.tsx`/`AcceptInvite.tsx`: wrap the existing `<form>` in `<AuthLayout title="…">`, remove the bare `<h1>` (AuthLayout owns it), wrap each input in the existing `.field` structure (label + input), set **`type="text"`** explicitly on Username / Existing-access-token inputs, and give the error `<p role="alert">` the `.field-err` class. Do NOT change the form `data-testid`, the input `aria-label`s, or the submit button text.
- [ ] **Step 4 — CSS.** Append to `styles.css` a theme-aware auth section (see spec §A): `.auth-screen{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;min-height:100dvh;padding:24px;overflow-y:auto}` and `.auth-card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);width:100%;max-width:420px;padding:clamp(20px,5vw,32px)}` + `.auth-card h1{...}` + `.auth-card .btn-primary{width:100%}`. Use only existing CSS variables (no hard-coded colors).
- [ ] **Step 5 — verify.** `cd ems/web/frontend && npx tsc --noEmit && npm run build` (report gz sizes, ≤300 KB) and `EMS_E2E_APP_PORT=8091 EMS_E2E_AUTH_PORT=8092 npx playwright test e2e/auth.spec.ts` → the full auth file passes (onboarding→login→logout, invite-accept, account-tokens all still green + the `auth-card` assertion).
- [ ] **Step 6 — commit.** `git add ems/web/frontend/src/AuthLayout.tsx ems/web/frontend/src/{Login,Onboarding,AcceptInvite}.tsx ems/web/frontend/src/styles.css ems/web/frontend/e2e/auth.spec.ts && git commit -m "feat(web): style login/onboarding/accept-invite via shared AuthLayout"`

**Acceptance:** every existing auth e2e green; `auth-card` visible/centered; no testid/label/button-text change; build under budget.

---

### Task 2 (I2) — Restart backend: safety core + endpoint  *(author: **Opus** · reviewer: Sol; safety-critical)*

This is the load-bearing safety task. Implement strictly to the spec's §B.0–B.3 and the Global Constraints. Read the spec first.

**Files:**
- Modify: `ems/control/service.py` (writer-registry reservation at every battery-write submission: control tick, override cycles, overrun-AUTO → moved onto a worker; single-admission), `ems/control/mode_controller.py` (set the "unconfirmed" flag on `BatteryWriteUnconfirmed`; clear on confirmed acceptance incl. AUTO), `ems/storage/control_state.py` (persist the unconfirmed flag), `ems/web/api.py` (`app.state.boot_id`; boot fingerprint after settings load; `_is_supervised()`; `restart_available`/`restart_pending`; register `/api/system/restart` only when `auth_store`; `/health/live` gains `boot_id`; add the path to the write-gating invariant), `ems/web/authz.py` (`ADMIN_PATHS` + `_SESSION_ONLY_PATHS` += `/api/system/restart`), `scripts/install.sh` (plist `EMS_SUPERVISED=1`), `docker-compose.dev.yml` (+ any prod compose) (`EMS_SUPERVISED=1`)
- Create: `ems/web/routes/system.py` (the restart handler) if a new router fits the existing `routes/` pattern; else fold into `api.py`.
- Test: `ems/tests/test_system_restart.py` (new), extend `ems/tests/test_authz.py`, `ems/tests/test_config.py` if needed, and a `ems/tests/test_shutdown_integration.py` (subprocess).

**Interfaces — Produces:**
- `authz.py`: `/api/system/restart` ∈ `ADMIN_PATHS` and `_SESSION_ONLY_PATHS`.
- A control-layer **writer registry** with a synchronous reserve at submission + release-on-real-completion, and an `idle_for_restart()`-style read (registry-empty ∧ not-unconfirmed ∧ confirmed-AUTO) exposed to the web layer.
- `POST /api/system/restart` → 202 `{"restarting":true,"boot_id":str}` | 409 (busy / unsupervised / already-restarting) | 403 (non-admin/non-session).
- `/health/live` → `{"status":"alive","boot_id":str}`.
- `restart_available: {available:bool, reason:str}` + `restart_pending: bool` on the endpoint the frontend reads (spec §B.3 — implementer picks/extends the least-invasive existing one, e.g. `/api/auth/me`).
- `app.state.request_restart` — injectable callable (default self-SIGTERM); tests replace with a spy.

- [ ] **Step 1 — failing tests first (TDD).** Write `test_system_restart.py` covering, at minimum:
  - **Gating:** 403 non-admin session; 403 admin **access token** (session-only); 409 when `EMS_SUPERVISED` unset; 409 second concurrent request (single-flight); route **absent** when `create_app(auth_store=None)`.
  - **Idle-and-safe (the safety core):** 409 with `reason` when the registry is non-empty — (i) a simulated **timed-out control worker still running** (a `to_thread` that outlives the `wait_for`; the slot stays reserved), (ii) **single-admission** — a second control tick is refused/coalesced while the first slot is outstanding, plus a **heterogeneous multi-slot** case (control tick + override outstanding together), (iii) an **overrun-AUTO worker admitted-but-not-yet-writing**; 409 when the persisted **unconfirmed flag** is set even though `last_confirmed_action` is AUTO/None; 409 for a non-AUTO `last_confirmed_action`; 409 for a pending override task. **202 + `request_restart` spy invoked + audit row** only when registry-empty ∧ not-unconfirmed ∧ confirmed-AUTO ∧ single-flight (incl. dry-run/not-operational → always safe).
  - **`_is_supervised()` unit:** `"1"/"true"/"yes"/"on"` (any case) → True; `"0"/"false"/""/unset/"maybe"` → False.
  - **`restart_pending`:** false at boot when nothing changed; true after a restart-tagged setting changes vs the post-load fingerprint; false for a live-tagged setting change.
  - **Slot lifecycle:** the registry slot is reserved at submission (before spawn) and released ONLY on the worker's real completion, never on the `wait_for` timeout (assert with a controllable fake worker).
  Run them → FAIL (endpoint 404, registry/flag absent).
- [ ] **Step 2 — subprocess integration test.** In `test_shutdown_integration.py`, launch a harmless instrumented app under the **pinned uvicorn** as a subprocess (an app whose lifespan writes a marker file on shutdown and whose `request_restart` is the real self-SIGTERM), `POST /api/system/restart`, and assert: the 202 body is delivered, the shutdown marker was written (lifespan `finally` ran), and the process exits — i.e. response-before-exit ordering. Run → FAIL.
- [ ] **Step 3 — implement the control-layer safety core.** Per the spec: the writer registry + reserve-at-submission at all three write paths (move overrun-AUTO onto a worker) + single-admission; the persisted unconfirmed flag (set on `BatteryWriteUnconfirmed`, cleared on confirmed acceptance incl. AUTO); the `idle_for_restart()` read. Run the idle-and-safe + slot-lifecycle tests → PASS. **Run the FULL control suite** (`uv run pytest ems/tests/test_control_service.py ems/tests/test_mode_controller.py ems/tests/test_car_mode.py -q`) to prove no control regression.
- [ ] **Step 4 — implement the web layer.** `boot_id`; post-load fingerprint; `_is_supervised()`; `restart_available`/`restart_pending`; the ADMIN+session `/api/system/restart` handler (single-flight synchronous check-and-set → idle-and-safe → audit → 202+boot_id → response-first `request_restart`; failing trigger clears flags); `/health/live` boot_id; authz sets; write-gating invariant; conditional registration. Run the gating + endpoint + subprocess tests → PASS.
- [ ] **Step 5 — supervisor env.** Add `EMS_SUPERVISED=1` to the launchd plist `EnvironmentVariables` in `scripts/install.sh` and to `docker-compose.dev.yml` (and any prod compose) env.
- [ ] **Step 6 — full verify.** `uv run pytest ems -q` (0 failures — run `uv run pytest ems` without extra `-q` for the summary line) and `uv run ruff check ems` clean.
- [ ] **Step 7 — commit.** `git add -A && git commit -m "feat(system): refuse-when-busy self-restart + writer-registry safety core + /api/system/restart"`

**Acceptance:** all Step-1/2 tests pass; full control suite + `pytest ems` green; ruff clean; the write-gating invariant covers the new endpoint; no `os._exit`.

---

### Task 3 (I3) — Restart frontend UX  *(author: Sonnet · reviewer: Sol)*

**Files:**
- Modify: `ems/web/frontend/src/Settings.tsx` (Apply & restart control + confirm + boot_id poll + 409 handling), and the small read that surfaces `restart_available`/`restart_pending` (matching Task 2's choice)
- Test: `ems/web/frontend/e2e/` (extend the settings/manage spec or add `restart.spec.ts`)

**Interfaces — Consumes:** `restart_available: {available,reason}`, `restart_pending: bool`, `POST /api/system/restart` → 202 `{boot_id}` | 409, `/health/live` → `{boot_id}`.

- [ ] **Step 1 — failing e2e.** With mocked `restart_available.available:true` + `restart_pending:true`, saving a restart-tagged setting surfaces an **"Apply & restart"** button; clicking it opens a confirm; confirming POSTs `/api/system/restart` (mock 202 + boot_id `B`), shows **"Restarting…"**, polls `/health/live` (mock returns boot_id `B` then `C`), and reloads on the changed id. Separately: mocked `supervised:false` → the **manual-restart hint** (`scripts/restart.sh`), no button; mocked **409** → a "mid-adjustment, try again" toast, button stays. Run → FAIL.
- [ ] **Step 2 — implement.** Add the capability-gated button + guided confirm (reuse the existing risky-action confirm pattern; copy per spec §B.4), the POST + "Restarting…" state + `/health/live` boot_id poll (with a timeout + "reload manually" fallback) + reload, the 409-busy toast, and the unsupervised manual hint. Read `restart_available`/`restart_pending` from Task 2's endpoint.
- [ ] **Step 3 — verify.** `cd ems/web/frontend && npx tsc --noEmit && npm run build` (≤300 KB gz) and run the new/updated e2e → PASS. Confirm no reader-mode/other-settings regression.
- [ ] **Step 4 — commit.** `git add -A && git commit -m "feat(web): Apply & restart UX — capability-gated button, confirm, boot_id poll, 409 retry"`

**Acceptance:** button appears only when available+pending; confirm→202→boot_id-change→reload; 409 and unsupervised handled; build under budget; e2e green.

---

## Polishing rounds (after I1–I3)
- **P1 — cross-cutting review sweep:** Sol reviews the whole branch diff (`codex-delegate.sh review --base main`, high effort, runs tests); fix wave for any Critical/Important.
- **P2 — adversarial safety review of the restart path:** Fable deep-check + the `/panel` `senior-software-engineer` + `qa-lead` reviewers. Prove: idle-and-safe cannot pass with any live/admitted writer or non-AUTO/unconfirmed last state; gating unbypassable incl. legacy; supervised-guard + boot_id sound; response-first trigger fires post-body; a failing trigger doesn't wedge. Fix wave.
- **P3 — final whole-branch review + verify + PR:** `/panel` pre-merge; full `pytest ems` + build + e2e; PR into `main` (record the two human-owned deviations: refuse-when-busy scope + the deferred general-shutdown-race follow-up item).

## Self-Review
- **Spec coverage:** §A → Task 1. §B.0 registry+unconfirmed+no-yield → Task 2 Steps 1/3. §B.1 endpoint/gating/supervised/trigger → Task 2 Steps 1/4. §B.2 boot_id → Task 2 Step 4. §B.3 capability/fingerprint → Task 2 Steps 1/4. §B.4 frontend → Task 3. §Testing (idle-and-safe cases, `_is_supervised`, subprocess) → Task 2 Steps 1/2; auth e2e → Task 1; frontend e2e → Task 3. §Discovered (deferred item) → P3 PR note. All covered.
- **Placeholders:** the control-layer registry integration is specified by contract + exact test cases rather than verbatim code, because the weave into `run_cycle`/override/overrun depends on the current control code the Opus author reads — the tests pin correctness (the deliberate deferral, flagged). No TODOs.
- **Consistency:** `idle-and-safe`, the registry, the unconfirmed flag, `restart_available`/`restart_pending`, `boot_id`, `_is_supervised()`, `request_restart` — used identically across tasks and match the spec's names.
