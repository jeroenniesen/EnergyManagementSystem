# Economic Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize economic calculations behind an immutable snapshot while preserving planner, finance, and savings behavior.

**Architecture:** Introduce a pure `EconomicSnapshot` and factory, characterize current formulas, then migrate planner economics, finance, and savings through compatibility wrappers. Replay and EV advice remain unchanged in this batch.

**Tech Stack:** Python 3.12, dataclasses, existing `TariffPolicy`, pytest, FastAPI response payloads.

## Global Constraints

- Preserve tariff defaults, rounding, planner thresholds, API keys, and control behavior.
- Keep replay and EV advice behavior unchanged unless only a compatibility wrapper is touched.
- No battery writes, storage schema changes, or deployment changes.
- Do not stage unrelated untracked artifacts.

---

### Task 1: Add EconomicSnapshot and parity tests

**Files:** Create `ems/economics.py`; test `ems/tests/test_economics_snapshot.py`.

- [ ] Characterize current `delivered_cost`, `export_value`, and savings formulas across tariff models, fees, efficiency, degradation, risk, and negative prices.
- [ ] Implement frozen `EconomicSnapshot`, factory from `TariffPolicy`/settings, pure calculation methods, and serializable metadata.
- [ ] Assert exact/rounded parity with current formulas and test invalid/clamped inputs.
- [ ] Run focused tests and Ruff, then commit.

### Task 2: Migrate planner economics and savings

**Files:** Modify `ems/planner/economics.py`, `ems/savings.py`; tests under `ems/tests/`.

- [ ] Add failing parity tests for planner break-even/net-benefit decisions and daily savings.
- [ ] Route existing public helpers through `EconomicSnapshot` while preserving signatures for callers.
- [ ] Preserve no-trade behavior and existing explanation text/rounding.
- [ ] Run planner/savings tests and commit.

### Task 3: Migrate finance and expose assumptions

**Files:** Modify `ems/finance.py`, `ems/application/services/report.py`, `ems/web/models.py`; tests for finance/report/savings.

- [ ] Add finance parity fixtures for import fees, export fees, net-metering, spot-minus-tax, and fixed-feed-in.
- [ ] Use snapshots for measured import/export/degradation calculations without changing persisted schema or response keys.
- [ ] Include snapshot metadata in report/savings explanation payloads as additive fields.
- [ ] Validate API models accept the additive metadata and commit.

### Task 4: Full verification and final review

- [ ] Run `.venv/bin/pytest -q`, `.venv/bin/ruff check ems`, frontend build, and `git diff --check`.
- [ ] Review replay/EV call sites and document them as the next migration slice.
- [ ] Confirm no control/battery/storage behavior changed and inspect status for unrelated artifacts.

