# Storage Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make application persistence shutdown explicit, complete, and failure-isolating.

**Architecture:** `StorageContext.close()` owns repository shutdown and is called once from the
FastAPI lifespan after tasks and shutdown audit work. Existing repository internals remain intact.

## Global Constraints

- Preserve fail-safe shutdown ordering and battery AUTO restoration.
- Do not change SQLite schemas or synchronous store connection strategy.
- Do not stage unrelated artifacts.

### Task 1: Harden StorageContext

**Files:** `ems/storage/context.py`, `ems/tests/test_storage_context.py`.

- [ ] Add tests proving every repository is accounted for, optional close hooks run, repeated close is harmless, and one close failure does not prevent later stores.
- [ ] Implement optional sync/async close dispatch with collected/logged errors and retry-safe state.
- [ ] Run focused tests and Ruff; commit.

### Task 2: Wire application lifecycle

**Files:** `ems/web/api.py`, lifecycle/API tests.

- [ ] Pass `control_state` into `StorageContext.from_existing`.
- [ ] Call the storage boundary from lifespan shutdown after background tasks and audit work.
- [ ] Add ordering/idempotence tests without changing battery restore behavior.
- [ ] Run focused shutdown tests and commit.

### Task 3: Full verification

- [ ] Run full pytest, Ruff, frontend build, and diff checks.
- [ ] Confirm no control/battery/schema/deployment changes.

