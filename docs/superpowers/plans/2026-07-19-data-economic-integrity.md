# Data and Economic Integrity Implementation Plan

**Goal:** Implement backlog B-112, B-113, B-125, and B-115 so reporting integrates only observed time, energy-flow reporting preserves authoritative P1 measurements, invalid reconstructed loads cannot train the planner, and incomplete price horizons cannot drive battery control.

**Safety constraints:** Stored ingest measurements remain separate from derived values (with the
existing documented plausibility clamps); P1 is net grid flow; incomplete or uncertain live prices
return control to `AUTO`; no new battery writer; no control activation or dry-run setting changes.

## Test-driven iterations

1. Add a pure bounded zero-order-hold integrator. Test irregular cadence, duplicates, ordering, boundaries, long gaps, and observed-duration coverage before implementation.
2. Apply the integrator to daily finance. Test timestamp-weighted import/export, priced-duration coverage, partial windows, and DST-sized local days. Pass explicit window bounds/cadence from API call sites and bump the finance cache calculation version.
3. Apply the same integration contract to energy-flow reporting. Test missing derived rows, authoritative measured grid totals, EV fallback, and source/sink conservation.
4. Add reconstruction assessment and learning quarantine. Test material negatives, small-noise clamping, non-finite/implausible values, valid export, and ensure invalid rows never form a learned load profile.
5. Add a pure price-horizon validator. Test timezone awareness, finite values, 15-minute alignment, strict ordering, duplicates/gaps, and Amsterdam 92/96/100-slot DST days.
6. Gate live plan construction with the validator. Test that incomplete prices never reach planning and that an already forced battery receives the fail-safe self-consumption intent.
7. Add cross-path regression tests and update specification/backlog-facing documentation for the implemented contracts and compatibility behavior.

Each iteration follows red, observed failure, minimal green implementation, focused regression run, then refactor while green.

## Polishing rounds

1. **Correctness:** adversarially review interval ownership, UTC/local-day boundaries, sign changes, missing data, DST, and fail-safe transitions; add only reproducible edge-case tests.
2. **Integration:** compare finance, energy-flow, reporting, cache, API, and control consumers; remove duplicate calculations and keep compatibility fields stable.
3. **Maintainability:** simplify names and interfaces, check documentation and comments against the SPEC, run formatting/lint/diff checks, then the full backend suite.

## Verification

- Focused tests after every red/green iteration.
- `uv run ruff check ems`
- `uv run pytest ems/tests`
- `git diff --check`
- Independent code review before completion.
