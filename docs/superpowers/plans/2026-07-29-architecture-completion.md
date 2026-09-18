# Architecture Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish the remaining architecture hardening in one reviewed batch without changing runtime behavior.

**Architecture:** Extract command execution/reconciliation behind the existing ControlService façade; replace untyped callback seams with protocols; complete response models; add static adapter signature checks; and close test-quality gaps.

## Global Constraints

- Preserve all fail-safe and battery-writer invariants.
- No API/schema/deployment/production behavior changes.
- No live network or battery writes in tests.

### Task 1: Command execution and reconciliation boundaries

Create `ems/control/execution.py` and `ems/control/reconciliation.py`; delegate from `ControlService`; add parity tests.

### Task 2: Typed runtime seams

Create `ems/application/protocols.py`; replace remaining `Any`/string callback annotations where services consume them; add type-focused tests.

### Task 3: Complete API response models

Extend `ems/web/models.py` with nested report/finance/diagnostics/verification fields; add route contract fixtures for stale/empty/unavailable cases.

### Task 4: Static adapter signature checks

Add protocol signature/conformance checks using `inspect.signature`/typing without new dependencies; cover all current adapters.

### Task 5: E2E safety gaps and verification

Add explicit write spies, AUTO-recovery assertions, unconfirmed-timeout coverage, and local test helpers; then run full pytest, Ruff, frontend build, and diff checks.

