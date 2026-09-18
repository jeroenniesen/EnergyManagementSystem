# Architecture Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract application services and establish typed runtime/storage boundaries while preserving every existing API, control, and persistence behavior.

**Architecture:** Keep `create_app` as the composition root. Introduce an `ApplicationContext` and `StorageContext`, inject them into small application services, then move only plan/report/verification/diagnostics HTTP handlers into route modules. Existing repositories, schemas, safety gates, and battery writer remain unchanged.

**Tech Stack:** Python 3.12, FastAPI, dataclasses/typing, aiosqlite, pytest, existing route and store abstractions.

## Global Constraints

- `SPEC.md` is the source of truth.
- Battery writes remain exclusively in `ems/sources/battery.py`.
- Fail-safe behavior and `control.dry_run` remain unchanged.
- No SQLite schema or settings-key changes.
- Preserve endpoint paths, auth rules, status codes, and JSON keys.
- Do not stage unrelated untracked artifacts.

---

### Task 1: Add typed application and storage contexts

**Files:**
- Create: `ems/application/__init__.py`
- Create: `ems/application/context.py`
- Create: `ems/storage/context.py`
- Modify: `ems/web/api.py` (context construction only)
- Test: `ems/tests/test_application_context.py`
- Test: `ems/tests/test_storage_context.py`

**Interfaces:**
- `ApplicationContext` is a dataclass containing existing source, controller, recorder, freshness, settings, and repository dependencies plus named runtime-state holders.
- `StorageContext.from_existing(...)` accepts already-constructed stores for tests and `StorageContext.create(db_path, ...)` constructs the current repositories.
- `StorageContext.close()` closes each present store once and is safe to call repeatedly.

- [ ] **Step 1: Write failing context tests** for construction, dependency identity, missing optional stores, and idempotent close.
- [ ] **Step 2: Run** `.venv/bin/pytest -q ems/tests/test_application_context.py ems/tests/test_storage_context.py`; verify the new interfaces fail before implementation.
- [ ] **Step 3: Implement** the dataclasses and storage factory using the current store constructors and close methods; do not alter store internals.
- [ ] **Step 4: Wire** `create_app` to create one context while keeping existing local aliases temporarily.
- [ ] **Step 5: Run** the focused tests and `.venv/bin/ruff check ems/application ems/storage/context.py ems/web/api.py`.
- [ ] **Step 6: Commit** with `git add ems/application ems/storage/context.py ems/web/api.py ems/tests/test_application_context.py ems/tests/test_storage_context.py && git commit -m "Add typed application and storage contexts"`.

### Task 2: Extract plan and verification services

**Files:**
- Create: `ems/application/services/__init__.py`
- Create: `ems/application/services/plan.py`
- Create: `ems/application/services/verification.py`
- Modify: `ems/web/api.py` (delegate only; no behavior changes)
- Test: `ems/tests/test_plan_service.py`
- Test: `ems/tests/test_verification_service.py`

**Interfaces:**
- `PlanService.get_plan(requested_window: ..., settings: ...) -> dict[str, object]` returns the existing plan payload.
- `VerificationService.verify(...) -> dict[str, object]` returns the existing verification payload and status discriminator.
- Both services receive `ApplicationContext` in their constructor and never receive FastAPI `Request` objects.

- [ ] **Step 1: Capture** current `/api/plan` and `/api/plan-verification` response fixtures from existing tests.
- [ ] **Step 2: Write failing service tests** using fake sources/stores and assert payload parity for `no_plan`, `awaiting_measurement`, and `observed` cases.
- [ ] **Step 3: Move** orchestration from the corresponding closures into the services, retaining existing pure helpers and safety checks.
- [ ] **Step 4: Delegate** the existing handlers to the services without changing route decorators or auth dependencies.
- [ ] **Step 5: Run** focused service/API tests and compare serialized payloads to the captured fixtures.
- [ ] **Step 6: Commit** with `git add ems/application/services ems/web/api.py ems/tests/test_plan_service.py ems/tests/test_verification_service.py && git commit -m "Extract plan and verification services"`.

### Task 3: Extract report and diagnostics services

**Files:**
- Create: `ems/application/services/report.py`
- Create: `ems/application/services/diagnostics.py`
- Modify: `ems/web/api.py`
- Test: `ems/tests/test_report_service.py`
- Test: `ems/tests/test_diagnostics_service.py`

**Interfaces:**
- `ReportService` exposes the existing report, finance, and savings assembly methods used by the moved handlers.
- `DiagnosticsService.get_snapshot() -> dict[str, object]` assembles the existing diagnostics response without HTTP dependencies.

- [ ] **Step 1: Identify** the exact report/finance/savings/diagnostics handlers and their current collaborators with `rg` before editing.
- [ ] **Step 2: Write failing unit tests** for normal, stale-data, and storage-unavailable responses.
- [ ] **Step 3: Implement** service methods by moving orchestration only; keep tariff/economic calculations in their current modules.
- [ ] **Step 4: Delegate** handlers and preserve status/error translation at the route boundary.
- [ ] **Step 5: Run** focused tests plus the existing endpoint tests.
- [ ] **Step 6: Commit** with `git add ems/application/services ems/web/api.py ems/tests/test_report_service.py ems/tests/test_diagnostics_service.py && git commit -m "Extract report and diagnostics services"`.

### Task 4: Move handlers into dedicated route modules

**Files:**
- Create: `ems/web/routes/plan.py`
- Create: `ems/web/routes/report.py`
- Create: `ems/web/routes/verification.py`
- Create: `ems/web/routes/diagnostics.py`
- Modify: `ems/web/api.py` (router registration and removal of duplicate handlers)
- Test: existing API route tests plus new route-import tests if needed

**Interfaces:**
- Each router receives the `ApplicationContext` and service instances through a small explicit dependency function.
- Existing URL paths, methods, auth guards, response models/JSON keys, and status codes remain byte-for-byte compatible where fixtures cover them.

- [ ] **Step 1: Add characterization tests** for every route moved in this task, including unauthorized and unavailable cases.
- [ ] **Step 2: Create** routers using the existing `ems/web/routes/` style and dependency helpers.
- [ ] **Step 3: Register** routers from `create_app` and remove the old duplicate route definitions.
- [ ] **Step 4: Run** all affected API tests and the hermetic e2e endpoint harness.
- [ ] **Step 5: Run** `.venv/bin/pytest -q`, `.venv/bin/ruff check ems`, `npm run build` in `ems/web/frontend`, and `git diff --check`.
- [ ] **Step 6: Commit** with `git add ems/web/routes ems/web/api.py ems/tests && git commit -m "Move plan report verification and diagnostics routes"`.

### Task 5: Document extension points and prepare review

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-architecture-foundations-design.md` only if implementation decisions materially changed
- Create or modify: concise architecture guide under `docs/` if needed

- [ ] **Step 1: Verify** no endpoint, control, schema, or deployment behavior changed using the full test/build evidence.
- [ ] **Step 2: Document** context/service/storage ownership and the next backlog sequence.
- [ ] **Step 3: Run** `git status --short` and confirm unrelated untracked artifacts are not staged.
- [ ] **Step 4: Request** code review before opening a PR.

