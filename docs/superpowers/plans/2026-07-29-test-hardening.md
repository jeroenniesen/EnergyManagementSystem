# Layered Test Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Strengthen regression coverage for planner, API, and safety invariants using deterministic tests.

**Architecture:** Add independent test modules; production code remains unchanged. Use bounded deterministic case generation and existing hermetic TestClient fixtures.

## Global Constraints

- No production behavior or dependency changes.
- No live network, battery writes, or external credentials in tests.
- Preserve fail-safe assertions explicitly.

### Task 1: Planner and safety invariants

**Files:** Create `ems/tests/test_property_invariants.py`.

- [ ] Cover reserve floors, bounded SoC, nonnegative energy, and no unsafe discharge across deterministic generated inputs.
- [ ] Cover economic no-trade and break-even invariants.
- [ ] Run focused tests and commit.

### Task 2: API failure-state matrix

**Files:** Create/modify API contract tests.

- [ ] Parameterize empty, stale, unavailable, unauthorized, and dry-run responses for plan/report/diagnostics/control endpoints.
- [ ] Assert status codes, response-model validation, and safe fallback fields.
- [ ] Run focused tests and commit.

### Task 3: End-to-end safety paths

**Files:** Create `ems/tests/test_safety_e2e_matrix.py`.

- [ ] Cover dry-run no-write, switch-cap refusal, AUTO fallback, and shutdown restoration.
- [ ] Keep all drivers mocked and deterministic.
- [ ] Run focused tests and commit.

### Task 4: Full verification

- [ ] Run full pytest, Ruff, frontend build, and diff checks.

