# SAF-01 — Fence timed-out control work and make AUTO recovery final

**Status:** implemented; [PR #52](https://github.com/jeroenniesen/EnergyManagementSystem/pull/52) open · **Audit:** SAF-01 · **Base:** `main` after PR #51

## Problem

PR #51 added a reserve-at-submission writer registry. It truthfully keeps restart unavailable while
a battery-affecting worker is outstanding, but it does not serialize the physical write after an
`asyncio.wait_for` timeout. The awaiter releases `control_lock`; the `to_thread` worker continues.
`_handle_overrun()` or an immediate override can therefore enter `driver.apply()` concurrently, and
the older command can land after the newer safety command. A stale charge must never overwrite an
AUTO recovery, manual return-to-AUTO, car guard, or shutdown restoration.

Python cannot kill a running worker thread. A generation check can suppress work that has not yet
entered the driver, but cannot retract an HTTP request already executing. Correctness therefore
requires both logical fencing and serialization of the physical command boundary.

## Safety contract

1. Battery-affecting operations reserve a typed ticket synchronously before task/thread submission.
2. Tickets carry a monotonically increasing generation and an operation class: `routine`,
   `override`, `recovery`, or `shutdown`.
3. Admitting `override`, `recovery`, or `shutdown` supersedes every older routine generation.
4. A superseded operation that has not entered the physical command boundary exits without calling
   `driver.apply()`.
5. Exactly one operation may occupy the physical command boundary at a time. If an old driver call
   is already running, a newer recovery waits behind it and writes last.
6. After a timed-out control cycle, no new routine cycle is admitted until the stale worker and its
   AUTO recovery have both completed.
7. Timeout recovery is scheduled once per timed-out generation. Its outcome is reported as
   confirmed, rejected/unconfirmed, failed, or timed out; the original timeout is always audited.
8. Override and shutdown work are never silently dropped. They may wait behind an already-running
   unkillable driver call, but then execute before routine work and determine the final command.
9. Dry-run exercises admission/fencing decisions but never invokes the driver.
10. Existing mode-change limits, dwell, lifecycle readiness, one-writer ownership, and fail-safe
    AUTO rules remain authoritative.

## Architecture

Add `ems/control/command_fence.py`, a small synchronous coordination value object independent of
FastAPI and asyncio. `BatteryCommandFence` owns a `threading.Condition`, ticket generations,
supersession state, physical-boundary ownership, and the recovery-pending latch. Its public API is:

```python
class CommandClass(StrEnum):
    ROUTINE = "routine"
    OVERRIDE = "override"
    RECOVERY = "recovery"
    SHUTDOWN = "shutdown"

@dataclass(frozen=True)
class CommandTicket:
    token: object
    generation: int
    command_class: CommandClass

class BatteryCommandFence:
    def reserve(self, command_class: CommandClass, *, draining: bool = False) -> CommandTicket | None
    def admit_routine(self, *, draining: bool = False) -> CommandTicket | None
    def begin_recovery(self, stale: CommandTicket, *, draining: bool = False) -> CommandTicket | None
    def enter(self, ticket: CommandTicket, *, timeout_seconds: float | None = None) -> bool
    def leave(self, ticket: CommandTicket) -> None
    def release(self, ticket: CommandTicket) -> None
    def empty(self) -> bool
    def snapshot(self) -> FenceSnapshot
```

`enter()` blocks on the condition until the physical boundary is free. It then rechecks
supersession. A stale ticket returns `False`; a live ticket becomes the sole owner and returns
`True`. `leave()` releases physical ownership and wakes priority waiters. `release()` is idempotent
and removes the outstanding reservation only on real worker completion. Recovery is reserved before
the timed-out worker is allowed to release its reservation, so there is no registry-empty gap.

`ControlContext` stores one `BatteryCommandFence` rather than open-coded writer set/lock fields.
Compatibility wrappers (`writer_registry_empty`, restart idle snapshot) delegate to it. Tests and
callers stop mutating the registry directly.

`ControlService._tick_worker()` associates the ticket with its worker thread, but does not enter the
fence while sensing or planning. `ControlService._decide()` enters lazily immediately before the
first `ModeController.decide()` that can reach the physical writer, and the worker retains ownership
through the rest of that command batch. A hung sensor read can therefore time out without occupying
the command lane or blocking recovery/shutdown. `_overrun_auto_worker()` and override cycles use
recovery/override tickets and the same boundary. The async `control_lock` remains a cycle-
orchestration lock, not a physical-write safety primitive.

Shutdown restoration receives the same fence through `ControlService.restore_for_shutdown()`.
Lifespan shutdown first marks draining, supersedes routine work, waits through the bounded driver
timeouts, then issues AUTO through the serialized boundary. No new direct `driver.apply()` call is
added outside the existing one-writer/controller boundary; the temporary shutdown direct call is
moved behind the service method as part of this slice because otherwise SAF-01 cannot guarantee final
ordering.

## Data flow on timeout

1. Periodic cycle reserves routine ticket R1 and starts the tracked worker.
2. The async waiter times out; R1 remains outstanding.
3. The event-loop path atomically marks R1 superseded and reserves recovery ticket A2.
4. If R1 has not entered, it observes supersession and performs no write. If it is already inside,
   it finishes first because the request cannot be cancelled safely.
5. A2 enters next, commands AUTO, records the outcome, and releases.
6. Only after both tickets release may a new routine cycle be admitted.

An override follows the same ordering but uses `OVERRIDE`, ensuring it is queued immediately and
not deferred to the next periodic cadence.

## Failure handling

- Ticket/task spawn failure releases the pre-reserved ticket and wakes waiters.
- Worker cancellation never releases a ticket owned by a live thread.
- Driver rejection, exception, or `BatteryWriteUnconfirmed` releases physical ownership and the
  ticket in `finally`, preserves the controller's unconfirmed state, and keeps the failure visible.
- A recovery wait has a diagnostic timeout, but timeout does not cancel or release its live worker.
- Shutdown fence entry is bounded at 120 seconds (with a 125-second async envelope), covering the
  production driver's configured retry envelope while keeping graceful teardown finite.
- Draining refuses routine/override admission from HTTP paths; shutdown uses its dedicated class so
  the shutdown restoration itself cannot be blocked by the draining flag.
- No lock is held while calling the driver. The condition protects coordination state only.

## Testing

Tests use stateful recording drivers and real threads/events rather than timing sleeps where
possible.

- A blocked routine CHARGE that outlives the cycle deadline cannot finish after recovery AUTO; the
  observed command order is `CHARGE, AUTO`, and AUTO is final.
- If timeout occurs before the routine enters the boundary, the stale routine never calls the
  driver and recovery AUTO is the only command.
- An override submitted during a blocked routine waits and writes last.
- A second routine is refused while stale or recovery work remains outstanding.
- Two recovery requests for one generation coalesce to one AUTO.
- Rejected, unconfirmed, exception, and recovery-timeout cases release tickets without opening a
  concurrent-write gap and report truthful outcomes.
- Shutdown restoration waits behind an existing write and AUTO is final.
- Dry-run produces zero driver calls.
- Existing restart, override, controller, shutdown, fault-injection, and full backend suites remain
  green; ruff is clean.

## Delivery: five iterations and five polish loops

1. Contract, isolated baseline, and deterministic fail-first stale-writer reproduction.
2. `BatteryCommandFence` unit with generation, supersession, serialization, and recovery coalescing.
3. Control-cycle, overrun recovery, and override integration.
4. Shutdown integration plus adversarial end-to-end and dry-run tests.
5. Remove obsolete coordination state, document outcomes, and run full verification.

Polish loops independently cover: concurrency correctness; retained safety guards; cross-file and
persistence consumers; fault/timing/test quality; simplification plus final independent review.

## Non-goals

- Physical post-write mode confirmation (B-91).
- Full command-outcome service consolidation beyond the fence boundary (B-96).
- Device-side forced-mode watchdog / abrupt `SIGKILL` recovery (B-138).
- Changing planner behavior, write limits, dry-run defaults, or restart UX.
