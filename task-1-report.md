# Task 1 report: clock boundary

Implemented an injectable, timezone-aware clock boundary.

- Added `Clock`, `SystemClock`, and `FrozenClock` in `ems/clock.py`.
- Frozen instants reject naive datetimes and are normalized to UTC.
- Added `now_local()` conversion for site timezone consumers.
- Added `clock` to `ApplicationContext`, defaulting to `SystemClock`.
- Added focused tests for UTC awareness, timezone conversion, exact frozen output, validation, and context defaults.

Verification:

```
.venv/bin/pytest -q ems/tests/test_clock.py  # 5 passed
.venv/bin/ruff check ems/clock.py ems/application/context.py  # All checks passed
```
