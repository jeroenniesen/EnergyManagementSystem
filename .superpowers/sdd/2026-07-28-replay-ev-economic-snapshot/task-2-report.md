# Task 2 report — EV economics migration

Implemented the EV advisor migration to `EconomicSnapshot`.

- Surplus-slot export valuation now uses the shared immutable snapshot instead of the legacy
  planner helper.
- Added an optional export-fee input and wired the read-only EV endpoint to configured grid fees.
- Existing function signature defaults, recommendation ordering, output fields, and copy remain
  unchanged.
- Added parity coverage for fixed feed-in, export fees, negative prices, and missing forecasts.

Verification:

- `.venv/bin/python -m pytest -q ems/tests/test_ev_advisor.py` — 10 passed.
- `.venv/bin/ruff check ems/ev_advisor.py ems/tests/test_ev_advisor.py ems/web/api.py` — passed.
- `git diff --check` — passed.
