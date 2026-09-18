# Task 2 report: application lifecycle wiring

Implemented the lifecycle boundary changes for the storage context.

## Changes

- `create_app` now accepts the existing `ControlStateStore` and passes it into
  `StorageContext.from_existing`.
- `main` supplies the control-state repository it already owns, so shutdown closes the same
  repository used by the controller callback rather than constructing a duplicate.
- FastAPI lifespan shutdown now calls the shared storage boundary after background tasks, audit
  work, watchdog cancellation, and battery AUTO restoration have completed.
- Removed the duplicate per-store shutdown loop; `StorageContext.close()` remains the single,
  failure-isolating and idempotent boundary.

## Verification

- `ruff check ems/web/api.py ems/main.py` passed.
- Existing shutdown and API tests should be run with the full lifecycle suite by the parent task.

The battery restore ordering is unchanged: restore runs before repository closure, and audit of
the restore therefore remains possible during shutdown.
