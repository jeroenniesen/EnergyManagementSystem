# Architecture completion batch

**Status:** Approved design

This batch completes the remaining architectural hardening as five isolated, behavior-preserving
slices: command execution/reconciliation boundaries, typed runtime callbacks, complete nested API
contracts, static adapter signature checks, and E2E safety-test gaps.

## Invariants

- Preserve battery AUTO fallback, dry-run, reserve/dwell/switch caps, writer fencing, and one-writer ownership.
- Preserve API paths, status codes, JSON keys, adapter behavior, and deployment.
- Production behavior changes are prohibited; tests and type boundaries are the primary deliverables.


## Review follow-up (2026-09-17)

The extracted services now have explicit typed collaborators; response models validate known
nested money, plan, verification and diagnostic fields while retaining additive fields.
One injectable clock supplies the moved API services and their control-plan provider. This is
not whole-process time virtualization: hardware timestamps, housekeeping, and unmoved endpoints
keep their existing time sources. Report/diagnostic assembly still uses explicitly typed
collaborators backed by create_app; moving all orchestration is outside this batch.

Review regressions are covered through actual control ticks and API calls: an unchanged car-guard
setpoint must not consume the command budget, and savings must use the efficiency/wear/risk
assumptions published with the result. Adapter checks verify optionality and resolved annotations,
including negative fixtures, rather than only method names. Production defaults remain unchanged.
