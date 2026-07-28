# Task 2 report: plan and verification services

Implemented the first service extraction slice without changing route paths, authentication, JSON
shapes, planner decisions, or battery/control behavior.

## Changes

- Added `PlanService.get_plan`, a FastAPI-independent implementation of the existing `/api/plan`
  payload.
- Added `VerificationService.verify`, preserving the observational verification statuses and
  thresholds used by `/api/plan-verification`.
- Wired both handlers through services using the existing `ApplicationContext`; runtime callbacks
  retain the existing closure-owned dependencies and avoid duplicating orchestration state.
- Added a small service package initializer.

## Verification

- `.venv/bin/pytest -q ems/tests -k 'plan or verification'` — passed (128 tests).
- `.venv/bin/ruff check ems/application/services ems/web/api.py` — passed.
- `git diff --check` — passed.

No unrelated artifacts were staged.
