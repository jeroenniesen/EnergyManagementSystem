# Task 1 report: typed application and storage contexts

## Status

Complete. The context seam is construction-only and does not change planner, control, persistence
schemas, or endpoint behavior.

## Files

- Added `ems/application/context.py` and package export in `ems/application/__init__.py`.
- Added `ems/storage/context.py` with `from_existing`, `create`, and idempotent async `close`.
- Wired `create_app` to expose one `ApplicationContext` at `app.state.application_context`, while
  retaining all existing local aliases and `AppContext` route wiring.
- Added focused application/storage context tests.

## Tests and checks

- `.venv/bin/pytest -q ems/tests/test_application_context.py ems/tests/test_storage_context.py` — 3 passed.
- `.venv/bin/pytest -q ems/tests/test_api.py` — 25 passed.
- `.venv/bin/ruff check ems/application ems/storage/context.py ems/web/api.py` — passed.

## Concerns / follow-up

- `StorageContext.close()` currently closes async stores; synchronous cache and control-state stores
  own per-operation connections and require no close operation.
- Existing shutdown code remains the lifecycle owner. A later task can migrate it to the storage
  context once service extraction is complete.
- `ApplicationContext` uses `Any` for legacy collaborators intentionally; service tasks can narrow
  these types incrementally without introducing import cycles.
