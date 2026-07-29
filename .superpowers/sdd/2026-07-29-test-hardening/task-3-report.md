# Task 3 report: end-to-end safety paths

Added `ems/tests/test_safety_e2e_matrix.py` with deterministic, in-memory battery drivers covering:

- Dry-run commands never write to the driver.
- A spent daily switch cap refuses a command without touching the driver.
- A rejected write recovers the battery to `AUTO`.
- Graceful shutdown restoration places a previously charging battery back in `AUTO`.

Focused verification: `.venv/bin/pytest -q ems/tests/test_safety_e2e_matrix.py` (4 passed).
