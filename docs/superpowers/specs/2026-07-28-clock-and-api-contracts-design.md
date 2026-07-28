# Deterministic clock and typed API contracts

**Date:** 2026-07-28  
**Status:** Approved design  
**Scope:** Second architecture-improvement batch

## Context

Time acquisition is spread across the application, while the existing time-window helpers already
encode important local-calendar and UTC-boundary rules. The extracted application services also
return untyped dictionaries, so endpoint contracts are enforced mostly by tests and convention.

## Goals

- Make time acquisition deterministic in application services and moved routes.
- Preserve existing local-calendar window semantics and UTC response serialization.
- Add explicit response models for plan, verification, report, finance, savings, and diagnostics.
- Add DST-boundary and frozen-clock coverage.

## Non-goals

- No mass rewrite of every `datetime.now()` call in hardware, storage, replay, or control internals.
- No planner, tariff, battery, or control behavior changes.
- No changes to JSON field names, endpoint paths, status codes, or settings keys.

## Design

### Clock boundary

Add `ems/clock.py`:

- `Clock` protocol with `now_utc() -> datetime` and `now_local(tz) -> datetime`.
- `SystemClock` using aware UTC time and the requested site timezone.
- `FrozenClock` for deterministic tests.

Extend `ems/timeutil.py` with small helpers that accept an explicit clock/anchor where needed.
The existing `resolve_window` behavior remains authoritative for local calendar periods and all
serialized API window boundaries remain UTC.

`ApplicationContext` receives a clock, defaulting to `SystemClock` at composition time. Extracted
services use it when a caller does not provide an explicit `now`; route handlers pass the context
clock. Existing explicit `now` arguments continue to win, so replay and current tests remain
compatible.

### API models

Add `ems/web/models.py` using the project's existing validation stack (Pydantic/FastAPI). Define
models for the response surfaces moved in the previous batch:

- plan and plan slots;
- verification result;
- report;
- finance and savings;
- diagnostics.

Models should allow the existing optional/null fields and preserve aliases only where current JSON
keys require them. Routes declare `response_model`; service internals may continue to assemble
dictionaries during migration. Validation failures remain normal server errors and do not alter
control behavior.

## Testing

- Unit-test `SystemClock` timezone conversion and `FrozenClock` exact values.
- Test Amsterdam DST transition windows and UTC serialization.
- Validate every extracted route response against its model, including empty/no-plan and unavailable
  cases.
- Run the full backend suite, Ruff, frontend build, and diff checks.

## Delivery sequence

1. Add clock implementations and tests.
2. Inject the clock into `ApplicationContext`, services, and routes.
3. Add response models and route declarations.
4. Add DST/contract tests and run full verification.

