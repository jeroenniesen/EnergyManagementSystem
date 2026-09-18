# Control decision boundaries

**Status:** Approved design

`ControlService` currently combines read coalescing, planning, intent guards, safety validation,
command admission, battery writes, confirmation, and shutdown reconciliation. This slice extracts
only the pure decision and safety boundaries behind the existing façade.

## Scope

- Add a pure `ControlDecisionEngine` for strategy/intent/effective-intent decisions.
- Add a `SafetyValidator` façade for freshness, reserve, dwell, switch-cap, and fail-safe checks.
- Keep `ControlService` method signatures, writer fencing, `ModeController`, battery writes,
  confirmation, and shutdown restore unchanged.
- Add parity tests for decisions and writes.

## Non-goals

No planner strategy changes, battery behavior changes, command-loop redesign, or API changes.

