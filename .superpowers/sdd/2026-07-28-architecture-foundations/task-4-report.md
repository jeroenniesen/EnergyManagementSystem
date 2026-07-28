# Task 4 report: dedicated plan/report/verification/diagnostics routes

Implemented the route extraction slice without changing endpoint contracts:

- Added dedicated routers for `/api/plan`, `/api/report`, `/api/finance`, `/api/savings`,
  `/api/plan-verification`, and `/api/diagnostics`.
- Routers receive the existing `AppContext` plus the already-constructed application service.
- Removed direct FastAPI registration for the moved handlers; orchestration and response assembly
  remain in the services and existing helpers.
- Preserved paths, methods, query validation, JSON responses, and status handling (including the
  invalid-date 422 response).

Validation run:

- `.venv/bin/pytest -q ems/tests/test_api.py` — passed (25 tests).
- `.venv/bin/ruff check --fix ...` — passed.
- `git diff --check` — passed.

The full suite, frontend build, and hermetic endpoint harness remain for the parent task's final
verification pass.
