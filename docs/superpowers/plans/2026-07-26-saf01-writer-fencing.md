# SAF-01 Writer Fencing Implementation Plan

> **Execution status:** completed in five implementation iterations and five polishing/fixing loops.
> Steps use checkbox syntax to preserve the verification record.

**Goal:** Guarantee that stale timed-out battery work cannot overwrite a newer AUTO recovery,
override, or shutdown restoration.

**Architecture:** A synchronous `BatteryCommandFence` gives every battery-affecting operation a
typed generation ticket and serializes the physical command boundary. `ControlService` retains
async orchestration while the fence, not an asyncio waiter lifetime, owns write ordering and
supersession. Recovery is reserved before stale work can disappear, and shutdown uses the same
boundary so AUTO is final.

**Tech Stack:** Python 3.12, asyncio, threading.Condition, pytest, FastAPI lifecycle wiring, ruff.

## Global Constraints

- All physical writes remain mode changes through the one battery writer; never add power tracking.
- AUTO remains the fail-safe whenever state or ordering is uncertain.
- Dry-run must perform zero physical writes.
- No lock may be held while network/device I/O executes.
- Driver calls remain bounded by their configured request timeout/retry policy.
- Do not alter dwell, daily switch caps, intent persistence, lifecycle readiness, or planning.
- Work in `feat/b89-writer-fencing`; do not stage unrelated shared-worktree files or commit/push
  unless the user asks.

---

### Iteration 1: Deterministic stale-writer reproduction and contract

**Files:**
- Create: `docs/superpowers/specs/2026-07-26-saf01-writer-fencing-design.md`
- Create: `docs/superpowers/plans/2026-07-26-saf01-writer-fencing.md`
- Modify: `ems/tests/test_control_service.py`

**Interfaces:**
- Consumes: current `ControlService.run_cycle()`, `_handle_overrun()`, and recording controller.
- Produces: deterministic failing tests proving stale CHARGE can currently land after AUTO and an
  override can overlap a timed-out worker.

- [x] Add a stateful blocking driver whose first non-AUTO application waits on `threading.Event`,
  records entry and completion order, and allows later AUTO calls to proceed.
- [x] Add `test_timeout_recovery_auto_is_final_after_blocked_stale_write`: use a short patched cycle
  budget, start a routine worker, wait for its driver entry, allow the waiter to time out, then
  release the stale call and assert final command order is `CHARGE, AUTO`.
- [x] Run the exact test and confirm RED because current recovery can complete before stale CHARGE,
  making CHARGE final.
- [x] Add `test_override_waits_for_timed_out_writer_and_writes_last` with events rather than sleeps.
- [x] Run the exact test and confirm RED because `control_lock` is released with the timed-out
  waiter while the worker remains live.
- [x] Keep production unchanged; record the two expected failures in the iteration notes.

### Iteration 2: Implement the synchronous command fence

**Files:**
- Create: `ems/control/command_fence.py`
- Create: `ems/tests/test_command_fence.py`

**Interfaces:**
- Produces: `CommandClass`, `CommandTicket`, `FenceSnapshot`, and `BatteryCommandFence` with
  `reserve`, `admit_routine`, `begin_recovery`, `enter`, `leave`, `release`, `empty`, `snapshot`.
- Consumes: only Python standard-library synchronization primitives.

- [x] Write a failing test that only one ticket can own the physical boundary and a waiting ticket
  enters after the owner leaves.
- [x] Run it and confirm RED because the module is absent.
- [x] Implement ticket reservation, condition-based physical ownership, and idempotent release.
- [x] Run the focused test and confirm GREEN.
- [x] Write failing tests for routine single-admission, supersession-before-entry, recovery
  coalescing per stale generation, priority ordering, draining admission, and snapshots.
- [x] Run them and confirm the expected behavioral failures.
- [x] Implement minimal generation/supersession/recovery state; never hold the condition during the
  caller's driver I/O.
- [x] Run `pytest ems/tests/test_command_fence.py -q` and confirm GREEN.
- [x] Run `ruff check ems/control/command_fence.py ems/tests/test_command_fence.py`.

### Iteration 3: Integrate periodic, recovery, and override operations

**Files:**
- Modify: `ems/control/service.py`
- Modify: `ems/web/api.py`
- Modify: `ems/tests/test_control_service.py`
- Modify: `ems/tests/test_system_restart.py`

**Interfaces:**
- Consumes: `BatteryCommandFence` ticket lifecycle from iteration 2.
- Produces: routine/recovery/override ticket admission and one serialized physical-operation
  boundary; existing restart queries delegate to fence snapshots.

- [x] Change iteration-1 tests only enough to use the new deterministic ticket boundary; run them
  and confirm they still fail before integration.
- [x] Replace `ControlContext.writer_registry/writer_lock` with one `command_fence` field and update
  reserve/release/restart compatibility methods.
- [x] Wrap `_tick_worker` in `enter/leave/release`; a superseded pre-entry routine returns no records.
- [x] On timeout call `begin_recovery(stale_ticket)` before handling overrun, and pass the reserved
  recovery ticket into `_handle_overrun`; never create a second recovery for the same generation.
- [x] Wrap `_overrun_auto_worker` in the same `enter/leave/release` lifecycle.
- [x] Admit overrides as `CommandClass.OVERRIDE`; retain submission-time reservation and orphan
  cleanup, but make the worker enter the shared physical boundary.
- [x] Run iteration-1 tests and confirm GREEN with AUTO/override final.
- [x] Update restart tests to assert via `FenceSnapshot` rather than private set mutation.
- [x] Run `pytest ems/tests/test_command_fence.py ems/tests/test_control_service.py ems/tests/test_system_restart.py -q`.
- [x] Run ruff on modified files.

### Iteration 4: Shutdown and adversarial safety integration

**Files:**
- Modify: `ems/control/service.py`
- Modify: `ems/web/api.py`
- Modify: `ems/tests/test_shutdown_restore.py`
- Modify: `ems/tests/test_fault_injection.py`
- Modify: `ems/tests/test_system_restart.py`

**Interfaces:**
- Produces: `ControlService.restore_for_shutdown(target, timeout_seconds) -> bool`, serialized as
  `CommandClass.SHUTDOWN` and final relative to every older ticket.
- Consumes: existing controller state reconciliation and audit behavior.

- [x] Write failing shutdown test: block old CHARGE, begin shutdown, release CHARGE, assert shutdown
  AUTO runs last and no later command occurs.
- [x] Run and confirm RED against the direct lifecycle `driver.apply` path.
- [x] Implement shutdown reservation/supersession and move `_shutdown_restore` device I/O through
  `ControlService.restore_for_shutdown`; preserve target sanitization and `note_confirmed_auto`.
- [x] Run the shutdown test and confirm GREEN.
- [x] Add fail-first cases for stale-before-entry suppression, driver exception, unconfirmed AUTO,
  recovery diagnostic timeout, recovery spawn failure, dry-run zero writes, and second-routine
  refusal throughout stale+recovery lifetime.
- [x] Implement only the lifecycle/outcome handling required by those cases.
- [x] Run shutdown, fault-injection, restart, controller, and service suites.
- [x] Run ruff on all changed Python files.

### Iteration 5: Consolidation, documentation, and broad verification

**Files:**
- Modify: `ems/control/service.py`
- Modify: `ems/web/api.py`
- Modify: `BACKLOG.md`
- Modify: `docs/failure-modes.md`
- Modify: `docs/superpowers/specs/2026-07-26-saf01-writer-fencing-design.md`

**Interfaces:**
- Produces: final documented SAF-01 behavior with obsolete open-coded coordination removed.

- [x] Search for direct production `driver.apply`, old registry/lock assumptions, and write paths
  outside the fence; resolve every SAF-01-relevant occurrence or document a deliberate B-91/B-96
  follow-up.
- [x] Remove obsolete registry fields/helpers and misleading comments; keep narrow compatibility
  methods only where API/restart consumers require them.
- [x] Update failure-mode documentation with stale-worker fencing and recovery-final semantics.
- [x] Record SAF-01 completion only after all acceptance evidence is available; do not change the
  merged backlog's unrelated B-89 auth item.
- [x] Run focused safety tests, full backend suite, and ruff.
- [x] Run `git diff --check` and inspect the complete diff.

## Polish / fix loop 1: Concurrency correctness

- [x] Trace every ticket state transition and all lock acquisition orders line by line.
- [x] Adversarially test pre-entry, in-driver, post-driver, cancellation, and spawn-failure windows.
- [x] Fix verified races using test-first red/green cycles; rerun focused suites.

## Polish / fix loop 2: Retained safety guards

- [x] Compare the diff against lifecycle readiness, dry-run, AUTO bypass, dwell, cap, intent
  persistence, restart draining, and unconfirmed-state guards.
- [x] Add fail-first tests for any dropped or weakened guard, fix, and rerun controller/service tests.

## Polish / fix loop 3: Cross-file and persistence consumers

- [x] Trace API overrides, restart coordinator, lifespan shutdown, control-state persistence, audit,
  and tests for stale assumptions.
- [x] Fix mismatches test-first and verify restart/shutdown integration.

## Polish / fix loop 4: Fault timing and test quality

- [x] Replace timing-dependent sleeps with events/barriers where possible.
- [x] Prove each regression test fails when its production fix is locally reversed or bypassed.
- [x] Run the marked fault-injection suite and targeted stress repetition.

## Polish / fix loop 5: Simplification and independent review

- [x] Remove duplication and unnecessary compatibility surface without changing behavior.
- [x] Request independent reviews for correctness, safety guards, cross-file consumers, language
  pitfalls, and simplification; adversarially verify every candidate finding.
- [x] Fix all verified Critical/Important findings test-first.
- [x] Run the complete requirement-by-requirement completion audit and fresh final verification.
