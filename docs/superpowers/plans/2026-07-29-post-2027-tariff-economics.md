# Post-2027 tariff economics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply one consistent, fee-aware import/export valuation to planning, savings, reporting, and explanations.

**Architecture:** Add a pure `TariffPolicy`/normalization module. Convert raw price slots at API boundaries before planner/reporting calls, while retaining raw prices for display. Keep battery intent and hardware control unchanged.

**Tech Stack:** Python dataclasses, existing `PriceSlot`, rule-based planner, FastAPI, React/Vite, pytest.

---

### Task 1: Lock the tariff contract

**Files:** `ems/tariffs.py`, `ems/tests/test_tariffs.py`

- [ ] Write tests for zero fees, import fee inclusion, export fee subtraction, negative prices, and unknown policy fallback.
- [ ] Implement immutable normalized values and `TariffPolicy.normalize(price)` with no I/O.
- [ ] Run `.venv/bin/pytest -q ems/tests/test_tariffs.py`.

### Task 2: Add settings policy

**Files:** `ems/settings.py`, `ems/tests/test_settings.py`

- [ ] Add `grid_fees.tibber_total_includes_all`, `grid_fees.import_fee_eur_per_kwh`, and `grid_fees.export_fee_eur_per_kwh` defaults/validation if absent.
- [ ] Preserve current zero-fee behavior and validate non-negative fee inputs.
- [ ] Run settings tests.

### Task 3: Normalize planner inputs

**Files:** `ems/web/api.py`, `ems/planner/rule_based.py`, `ems/tests/test_rule_based.py`

- [ ] Add a boundary helper that maps each `PriceSlot` to its effective import price while retaining timestamps.
- [ ] Feed effective import prices into planner and what-if paths.
- [ ] Ensure negative prices remain negative and export value is never used as a forced export command.

### Task 4: Normalize savings and finance

**Files:** `ems/savings.py`, `ems/finance.py`, `ems/web/api.py`, tests

- [ ] Pass effective import/export values into savings and daily finance calculations.
- [ ] Add policy metadata to returned savings/report data.
- [ ] Cover asymmetric import/export fees.

### Task 5: Add plan economics diagnostics

**Files:** `ems/planner/explain.py`, `ems/web/api.py`, frontend plan types/components, tests

- [ ] Include import fee, export fee, and policy mode in plan explanation metadata.
- [ ] Explain that export is valued below import when fees are asymmetric.

### Task 6: Handle edge conditions

**Files:** `ems/tariffs.py`, planner/report tests

- [ ] Define missing-price behavior as `None`/skipped, not zero.
- [ ] Clamp no user-entered fee negative values through settings validation.
- [ ] Add regression tests for negative import and negative export values.

### Task 7: Update API and UI

**Files:** `ems/web/api.py`, `ems/web/frontend/src/Insights.tsx`, plan components, frontend tests

- [ ] Expose `tariff_policy` metadata in plan/report responses.
- [ ] Add a concise “2027 tariff assumptions” note in the relevant dashboard surface.
- [ ] Keep raw market prices visible for transparency.

### Task 8: Integration test matrix

**Files:** `ems/tests/test_api.py`, planner/report tests, frontend e2e tests

- [ ] Test legacy defaults, import-only fee, export-only fee, both fees, and negative prices end to end.
- [ ] Confirm dry-run and battery writer paths are unchanged.

### Task 9: Documentation

**Files:** `docs/config-reference.md`, `docs/api-reference.md`, `docs/energy-model.md`, README

- [ ] Document effective import/export valuation and 2027 assumptions.
- [ ] Document that tariff economics affects planning/reporting only, never direct power tracking.

### Task 10: Verification and release

- [ ] Run focused pytest, full pytest, Ruff, frontend build, and relevant e2e tests.
- [ ] Run `git diff --check`, review staged files, and prepare a PR summary with any unrelated failures.

### Polishing loops

- [ ] Polish 1: adversarial tariff math review.
- [ ] Polish 2: planner/no-trade and negative-price review.
- [ ] Polish 3: API/UI transparency and accessibility review.
- [ ] Polish 4: regression and dry-run safety review.
- [ ] Polish 5: documentation and release review.
