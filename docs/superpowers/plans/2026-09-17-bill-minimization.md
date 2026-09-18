# Electricity bill minimization implementation plan

> For agentic workers: use superpowers:subagent-driven-development.

**Goal:** Reduce avoidable electricity purchases and provide trustworthy evidence of net savings.
**Architecture:** Extend pure domain functions and the existing control/reporting/router seams.
**Tech Stack:** Python/FastAPI/SQLite, React/TypeScript/Vite, pytest and Playwright.
**Spec:** docs/superpowers/specs/2026-09-17-bill-minimization-design.md

## Global constraints

- One writer; mode intents, target SoC and deadline; unchanged safety validator.
- No car work, live settings changes, device writes, deployment, commits or pushes.
- New control behavior: explicit opt-in, default off, dry-run only pending acceptance.
- Evidence-gated read-only advice; euros and stored energy use explicit AC/DC units.

## Task 1: Planner economics

Own adaptive.py, rule_based.py, strategy.py, control/service.py and related planner tests.
Consume existing EconomicSnapshot/TariffPolicy; preserve public default behavior via config flags.
Produce strict net-positive per-slot purchase selection and chronological solar-aware winter
sizing. Root registers planner.bill_optimization_enabled and threads settings as required.
- [x] Reproduce marginal-loss summer charge and winter solar overbuy in failing tests.
- [x] Implement opt-in economics and chronological solar accounting without changing writer.
- [x] Exercise profitable/no-trade/late-solar/headroom/negative-price and default compatibility.

## Task 2: Tariff periods and reconciliation

Own tariffs.py, economics.py, finance.py, new tariff history storage/routes/tests. Do not edit
api.py/settings.py; send root integration instructions. Preserve old constructors/signatures.
Produce date-effective tariff resolution and authenticated read-only invoice reconciliation.
- [x] Test period boundaries, overlaps, non-finite values, components and legacy fallback.
- [x] Implement immutable stored periods and per-time tariff valuation for finance.
- [x] Test missing/partial data and fixed-charge treatment for invoice reconciliation.

## Task 3: Replay evidence

Own replay.py, web/routes/whatif.py and replay/whatif tests. Consume compatible existing economics.
Produce sequential rolling comparisons, explicit grid/wear/net costs and terminal energy.
- [x] Test absence of load foresight, day continuity/gaps and forecast issuance chronology.
- [x] Implement trailing history and rolling replans, scenario-specific energy continuity.
- [x] Expose net deltas and simulation limitations through the existing API.
- [x] Verify AUTO, no-battery and oracle baselines remain distinct and physical.

## Task 4: Recommendations and demand calibration

Root owns planner/load_profile.py, new bill_advice.py and routes/bill_advice.py, their tests,
settings.py and API integration. API contracts expose evidence-gated recommendations and
read-only appliance timing. No automatic adoption.
- [x] Test weekday/weekend weighting, held-out errors, insufficient calibration evidence.
- [x] Test reserve floors, stale data, night baseload/consumption comparisons.
- [x] Test contiguous appliance schedule, deadline, solar opportunity costs and missing prices.
- [x] Implement pure helpers and authenticated route assembly.

## Task 5: UI, docs and integration

Root owns Insights integration, new SavingsAdvice.tsx, WhatIf display and frontend tests.
- [x] Surface reserve/calibration/consumption advice with explicit unknown/error states.
- [x] Add appliance advice and invoice forms; no control writes from these flows.
- [x] Update counterfactual labels and display bill versus wear-adjusted benefit separately.
- [x] Update SPEC, BACKLOG and API documentation; keep real-world acceptance explicit.
- [x] Run affected tests, full backend tests/lint, frontend tests/build and hermetic UI/API.
- [x] Independent adversarial review and fix verified findings before reporting completion.

## Execution ledger

- Baseline: isolated worktree feat/bill-minimization at 3ea1ffb; original tree untouched.
- Authorization: user approved implementation of all six assessment recommendations.
- Fence: tasks have disjoint ownership; root coordinates shared API/settings/docs changes.

- Implemented all five tasks covering the six approved recommendations. No car functionality changed.
- Independent reviews covered planner/controller compatibility, tariff/finance history, and replay
  chronology. Verified findings were fixed, including contiguous charge targets, partial-slot
  projection duration, historical finance preservation, tariff gaps, and calibration evidence.
- Final verification: 1,859 backend tests passed; Ruff and git diff --check passed;
  32 frontend unit tests passed; production frontend build passed; 80 hermetic browser checks
  passed, including permissions, actual tariff/invoice requests, accessibility, and mobile layout.
- Inspected mobile screenshots for both new panels. No horizontal overflow.
- The opt-in strategy requires profitable future household demand even at negative prices;
  unconditional negative-price soaking remains outside this variant. Default behavior is preserved.
- Production acceptance remains outstanding by design: enter actual contract periods, reconcile
  household invoices, and compare multi-day dry-run decisions before considering live activation.
- Follow-up authorization: prepare a PR; commit and publish feat/bill-minimization from the isolated worktree.
- Base history: forked from 3ea1ffb, with 46 existing local architecture commits not yet on origin/main.
  Keep this dependency explicit when preparing the PR.
