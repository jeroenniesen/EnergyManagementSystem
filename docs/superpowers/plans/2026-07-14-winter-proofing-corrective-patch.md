# Winter-proofing Corrective Patch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the merged winter-proofing validator, recovery, hysteresis, and model-health correctness bugs without weakening existing fail-safe controls.

**Architecture:** Keep the existing planner/validator/recovery interfaces. Correct deadline and strategy scoping at their existing boundaries, make recovery a single per-cycle result, and preserve AUTO fallback and feature flags.

**Tech Stack:** Python, pytest, SQLite/settings store, existing Starlette APIs.

## Global Constraints

- Validator remains authoritative; rejected plans fall back to AUTO.
- Recovery may only select economically profitable slots and only before the applicable deadline.
- Existing settings and dry-run semantics remain compatible.
- No new runtime dependency is required.

### Task 1: Validator reachability and scope

**Files:** `ems/validator.py`, `ems/tests/test_validator.py`

- Write failing tests for a two-peak winter plan, summer exemption, configured target SOC, and exact five-point margin.
- Run the focused tests and confirm failure.
- Implement per-peak deadline reachability using the configured target and winter-only gating.
- Run validator tests and commit.

### Task 2: Recovery economics and deadline handling

**Files:** `ems/recovery.py`, `ems/tests/test_recovery.py`

- Write failing tests for break-even filtering, passed deadlines, multi-window sizing, and preservation of unused cheap slots.
- Run focused tests and confirm failure.
- Reuse the normal planner break-even ceiling; reject recovery after deadline; size catch-up by applicable peak/window.
- Run recovery tests and commit.

### Task 3: Hysteresis persistence and replay parity

**Files:** `ems/strategy.py`, `ems/replay.py`, `ems/tests/test_adaptive.py`, `ems/tests/test_strategy_api.py`, relevant replay tests

- Write failing tests proving reset is day-scoped and stable state refreshes TTL.
- Run tests and confirm failure.
- Guard reset and increment by day key, refresh persisted stable state, and align replay evaluation with live tick semantics.
- Run focused tests and commit.

### Task 4: Single recovery computation and daytime evidence

**Files:** `ems/api.py`, `ems/confidence.py`, `ems/analysis.py`, `ems/tests/test_recovery_wiring.py`, `ems/tests/test_confidence.py`, `ems/tests/test_analysis.py`

- Write failing tests proving execution/audit share one recovery result and that night slots do not dilute evidence.
- Run tests and confirm failure.
- Thread one computed recovery result through the cycle and filter model-health matched slots to daytime evidence.
- Run focused tests and commit.

### Task 5: Full verification

- Run `pytest ems/tests/test_validator.py ems/tests/test_recovery.py ems/tests/test_adaptive.py ems/tests/test_strategy_api.py ems/tests/test_recovery_wiring.py ems/tests/test_confidence.py ems/tests/test_analysis.py -q`.
- Run the full backend suite and `git diff --check`.
- Review settings-flag compatibility and document any environment-only blockers.
