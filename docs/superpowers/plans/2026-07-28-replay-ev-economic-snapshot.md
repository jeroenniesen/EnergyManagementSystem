# Replay and EV Economic Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete economic calculation consolidation for replay and EV advice without changing outputs.

**Architecture:** Build `EconomicSnapshot` instances from existing replay/EV settings and replace only legacy export valuation calls. Keep simulation and recommendation logic intact.

## Global Constraints

- Preserve replay schemas, EV output fields/copy, and all public signatures.
- Preserve tariff/export fee semantics, negative prices, and missing-price behavior.
- No control, battery, storage, or deployment changes.

### Task 1: Replay migration

**Files:** `ems/replay.py`; replay tests.

- [ ] Add parity fixtures for all export models and fee combinations.
- [ ] Add `EconomicSnapshot.from_replay_config` and use it for export credits.
- [ ] Run replay tests and commit.

### Task 2: EV migration

**Files:** `ems/ev_advisor.py`; EV tests.

- [ ] Add parity fixtures for surplus/non-surplus, export models, fees, negative prices, and missing forecasts.
- [ ] Build a snapshot from existing EV settings and use it for surplus slot cost.
- [ ] Run EV tests and commit.

### Task 3: Full verification

- [ ] Run full pytest, Ruff, frontend build, and diff checks.
- [ ] Confirm no replay/EV schema or UX changes.

