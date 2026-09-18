# Source Ports and Adapter Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make source boundaries explicit and verify existing adapters conform without changing runtime behavior.

**Architecture:** Add a central typed ports module that aliases existing domain protocols, then add hermetic conformance tests and update type annotations at composition boundaries only.

## Global Constraints

- Preserve existing public imports and runtime wiring.
- No live network calls in tests.
- Battery writes remain exclusively through the existing writer.

### Task 1: Centralize ports

**Files:** Create `ems/sources/ports.py`; update `ems/sources/base.py`, `battery.py`, `prices.py`, `forecast.py`; tests.

- [ ] Define/re-export the four typed protocols and preserve compatibility aliases.
- [ ] Update composition annotations only; do not alter implementations.
- [ ] Add protocol structural checks for current mocks/adapters.
- [ ] Run focused tests and commit.

### Task 2: Adapter conformance tests

**Files:** tests for mock, Tibber, Forecast.Solar, HomeWizard, Indevolt adapters.

- [ ] Add hermetic fake-client tests for required methods, normalized outputs, and failure-safe fallbacks.
- [ ] Verify battery adapter conformance without enabling writes.
- [ ] Run focused tests and commit.

### Task 3: Full verification

- [ ] Run full pytest, Ruff, frontend build, and diff checks.
- [ ] Confirm no network, control, battery, schema, or deployment behavior changed.

