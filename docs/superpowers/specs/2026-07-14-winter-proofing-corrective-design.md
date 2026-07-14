# Winter-proofing corrective patch design

## Goal

Correct the merged winter-proofing gates so valid multi-peak plans remain executable, recovery never buys uneconomic energy, and hysteresis remains stable across ticks and restarts.

## Design

- Validator check #6 applies only to winter plans. Reachability is evaluated per charge target/window using the peak-specific deadlines and the configured target SOC; it must not compare a whole-day target with the first peak.
- Recovery refuses work when the deadline is reached or passed. It reuses the normal planner's break-even ceiling and only selects remaining slots that can economically reduce the applicable peak shortfall. Recovery diagnostics and execution share one computed result per control cycle.
- Hysteresis accumulation and reset are both day-scoped. Stable persisted state is refreshed before its TTL expires, preserving season state across restarts.
- Model-health evidence uses daytime matched slots only. Replay tests exercise the same tick/day semantics as live hysteresis.

## Safety and compatibility

Single-writer control, validator authority, AUTO fallback, dry-run behavior, and existing feature flags remain unchanged. Existing settings remain backward compatible; no new user-facing controls are introduced.

## Verification

Add regression tests for each reported failure, including multi-peak winter reachability, summer exemption, break-even recovery, passed deadlines, per-day hysteresis reset, TTL refresh, single recovery computation, daytime evidence, and the fixed five-point margin. Run focused suites followed by the relevant full backend tests.
