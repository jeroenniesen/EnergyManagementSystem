# Control Decision Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Separate pure control decisions and safety checks from the stateful control façade without changing battery behavior.

**Architecture:** Extract decision and safety collaborators around the existing `ControlContext` and injected callables. `ControlService` remains the compatibility and command/writer façade.

## Global Constraints

- Preserve AUTO fail-safe, dry-run, reserve floor, dwell, switch-cap, writer fencing, and single battery writer.
- Preserve all public `ControlService` signatures and API behavior.
- No planner, storage, schema, or deployment changes.

### Task 1: Extract pure decision engine

**Files:** Create `ems/control/decision.py`; modify `ems/control/service.py`; tests.

- [ ] Characterize current effective-intent/strategy decisions and car guards.
- [ ] Implement `ControlDecisionEngine` using explicit context/callables, with no battery writes or FastAPI dependencies.
- [ ] Delegate from `ControlService` while preserving signatures and mutable state ownership.
- [ ] Run control/safety/car tests and commit.

### Task 2: Extract safety validator façade

**Files:** Create `ems/control/safety.py`; modify `ems/control/service.py`; tests.

- [ ] Characterize stale data, reserve, dwell, switch-cap, unsafe plan, and fail-safe outcomes.
- [ ] Implement `SafetyValidator` returning the existing safe decision/outcome values.
- [ ] Delegate validation from the façade; keep command admission and writer fencing in service.
- [ ] Run safety/system-restart/control tests and commit.

### Task 3: Full verification and review

- [ ] Run full pytest, Ruff, frontend build, and diff checks.
- [ ] Confirm battery writes remain only through existing writer path and no control thresholds changed.

