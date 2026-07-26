# Backup and Readiness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make backup validation and service readiness fail closed so production never reports healthy state from a missing, corrupt, or incomplete database.

**Architecture:** Keep backup verification as a standalone read-only script, expose a synchronous SQLite integrity probe on `HistoryStore`, and have `/health/ready` combine recorder health with storage integrity. Document the operator acceptance steps without changing battery-control behavior.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest, Ruff.

## Global Constraints

- All writes remain behind the existing storage/control boundaries.
- Readiness must fail closed on stale recorder state or failed integrity checks.
- No live battery writes or control-loop behavior changes are included.

### Task 1: Backup verifier

**Files:**
- Create: `scripts/verify_backup.py`
- Create: `ems/tests/test_verify_backup.py`

- [ ] Test valid SQLite backup returns success, missing file fails, and zero-byte file fails.
- [ ] Implement a read-only verifier using SQLite `PRAGMA integrity_check` and explicit non-zero-size checks.
- [ ] Run `.venv/bin/pytest -q ems/tests/test_verify_backup.py` and `.venv/bin/ruff check scripts ems`.

### Task 2: Readiness integrity probe

**Files:**
- Modify: `ems/storage/history.py`
- Modify: `ems/web/api.py`
- Test: `ems/tests/test_history.py`, `ems/tests/test_api.py`

- [ ] Add `HistoryStore.integrity_probe() -> bool` using a synchronous read-only SQLite connection.
- [ ] Make `/health/ready` return non-ready when recorder failure streak or integrity probe fails.
- [ ] Add valid/corrupt database and recorder-failure regression tests.
- [ ] Run focused API/history tests.

### Task 3: Release acceptance documentation

**Files:**
- Create: `docs/release-acceptance-checklist.md`

- [ ] Document backup verification, restore rehearsal, readiness checks, and explicit unresolved live-device drills.
- [ ] Review for concrete commands and no claims beyond available evidence.

### Task 4: Verification and PR

- [ ] Run `.venv/bin/pytest -q`, `.venv/bin/ruff check ems`, and `git diff --check`.
- [ ] Commit only the focused files on a branch based on current `main`.
- [ ] Push and create a PR with test evidence and remaining operational caveats.
