# Architecture foundations and service extraction

**Date:** 2026-07-28  
**Status:** Design approved; implementation plan next  
**Scope:** First architecture-improvement batch

## Context

The application is stable enough for incremental architecture work, but several responsibilities
are concentrated in a few large modules. In particular, `ems/web/api.py` owns application
composition, lifecycle tasks, authentication, settings overlays, planning, reporting, diagnostics,
and HTTP response shaping. SQLite stores each manage their own connection and locking policy.
This increases the cost and risk of extending the system, especially around lifecycle failures,
typed state, and endpoint changes.

The architecture review identified these ten improvements, in priority order:

1. Split web routing from application services.
2. Break control orchestration into read/coalescing, decision, safety, execution, and reconciliation
   boundaries.
3. Introduce typed runtime/application context objects.
4. Establish one persistence boundary and shared SQLite connection policy.
5. Make time and clock handling explicit and injectable.
6. Formalize ports and adapters for external sources.
7. Use explicit API response models.
8. Centralize economics and explainability around an economic snapshot.
9. Make runtime lifecycle and task ownership explicit.
10. Strengthen tests with property, contract, adapter-conformance, deterministic-clock, and focused
    safety tests.

This document covers the first batch: items 1, 3, and 4, with item 9 limited to the lifecycle
hooks needed by the new context and storage boundary. Items 2 and 5–10 remain follow-up work.

## Goals and non-goals

### Goals

- Reduce the responsibility of `ems/web/api.py` without changing public behavior.
- Introduce typed dependency and runtime-state boundaries that can be extended incrementally.
- Give SQLite repositories one shared composition and lifecycle boundary.
- Preserve all existing endpoint paths, authentication rules, status codes, response keys, and
  fail-safe behavior.
- Make the extracted services unit-testable without hardware, network calls, or a running FastAPI
  server.

### Non-goals

- No planner, tariff, control, or battery-behavior changes.
- No database schema migration or data-format change.
- No replacement of the existing SQLite repositories in this batch.
- No full dependency-injection framework or rewrite of `create_app`.
- No change to production deployment defaults.

## Design

### Application context

Add `ems/application/context.py` with a typed context that groups:

- source and planner dependencies;
- battery, controller, recorder, freshness, and stores;
- effective settings access;
- typed handles for plan/report/verification/diagnostic runtime state;
- task ownership and shutdown hooks needed by the composition root.

The context is an application boundary, not a global singleton. Tests can construct it with fakes.
Mutable state currently held in closure dictionaries remains mutable in place during migration, but
gets named fields and explicit ownership.

### Application services

Add services under `ems/application/services/`:

- `PlanService`: assemble and expose the current plan and plan-related calculations.
- `ReportService`: produce report/finance/savings data through existing collaborators.
- `VerificationService`: compare planned intent with observations and return the current
  verification result.
- `DiagnosticsService`: assemble health, freshness, storage, and runtime diagnostics.

Services contain orchestration and domain-result assembly. They do not depend on FastAPI request
objects and do not write to the battery. Existing pure helpers and safety gates remain in their
current modules until a later control-boundary slice.

### Web routes

Add route modules following the existing `ems/web/routes/` convention. Move the corresponding
handlers for plan, report, verification, and diagnostics into those modules. `create_app` remains
the composition root: it builds the context, services, and routers, and owns startup/shutdown.

During migration, compatibility wrappers are allowed where necessary, but each endpoint must have
one authoritative implementation. Public JSON shapes are characterized before moving and asserted
after moving.

### Persistence boundary

Add a storage composition boundary (for example `ems/storage/context.py`) that owns:

- the configured database path;
- construction of `HistoryStore`, `SettingsStore`, `AuditStore`, `AuthStore`, `CacheStore`, and
  related repositories;
- shared connection/lifecycle policy and orderly close;
- repository access exposed to the application context.

The existing stores remain responsible for their current SQL and self-healing behavior. This slice
does not merge tables or rewrite queries. The boundary removes construction and shutdown knowledge
from individual web closures and gives tests one place to provide fake repositories.

### Compatibility and failure behavior

- Existing settings keys and SQLite schemas remain unchanged.
- Missing or stale data continues to produce the existing safe responses.
- Storage initialization failures remain explicit and must not open write access or battery control.
- Service exceptions are translated at the route boundary using the existing status-code contract.
- Battery writes continue to use the single writer in `ems/sources/battery.py`.

## Testing strategy

1. Capture current response/status behavior for each moved endpoint with characterization tests.
2. Add service tests using fake stores/sources and deterministic input objects.
3. Add context/storage-boundary tests covering construction, partial initialization, and idempotent
   shutdown.
4. Run the full backend suite, lint, frontend build, and diff checks.
5. Exercise the relevant endpoints through the hermetic API/e2e harness.

## Delivery sequence

1. Introduce typed context and storage composition boundary with no route movement.
2. Add services and characterization tests.
3. Move plan/report/verification/diagnostics routes one surface at a time.
4. Remove duplicate closures and compatibility wrappers after tests prove parity.
5. Document the new extension points and prepare a focused PR.

## Follow-up backlog

After this batch, implement the remaining recommendations in this order:

1. Explicit clock/time-window abstractions.
2. Typed API response models.
3. Central `EconomicSnapshot` for economics and explanations.
4. Control-service decomposition around safety and execution.
5. Formal source ports/adapters.
6. Explicit runtime lifecycle/task supervisor.
7. Layered property, contract, adapter, and safety tests.

