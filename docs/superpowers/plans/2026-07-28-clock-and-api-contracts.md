# Deterministic Clock and Typed API Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make extracted application routes deterministic in time and enforce their response contracts without changing behavior.

**Architecture:** Add a small clock port with system/frozen implementations, inject it through `ApplicationContext` into the extracted services, then add Pydantic response models at the HTTP boundary. Existing explicit timestamps and domain dictionaries remain compatible during migration.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, `datetime`, `zoneinfo`, pytest.

## Global Constraints

- Preserve endpoint paths, auth, status codes, JSON names, and optional fields.
- Preserve local-calendar window resolution and UTC serialization.
- Do not change planner, tariff, battery, control, storage, or deployment behavior.
- Use aware datetimes; never introduce naive timestamps.
- Do not stage unrelated untracked artifacts.

---

### Task 1: Add the clock boundary

**Files:** Create `ems/clock.py`; modify `ems/application/context.py`; test `ems/tests/test_clock.py`.

- [ ] Write failing tests for `SystemClock.now_utc`, timezone conversion, and exact `FrozenClock` output.
- [ ] Implement `Clock`, `SystemClock`, and `FrozenClock` with aware datetime validation.
- [ ] Add a `clock` field to `ApplicationContext`, defaulting to `SystemClock` only when omitted.
- [ ] Run `.venv/bin/pytest -q ems/tests/test_clock.py` and `.venv/bin/ruff check ems/clock.py ems/application/context.py`.
- [ ] Commit the clock boundary and tests.

### Task 2: Inject clock into extracted services and routes

**Files:** Modify `ems/application/services/plan.py`, `verification.py`, `report.py`, `diagnostics.py`, and extracted route modules; test relevant service/API files.

- [ ] Add frozen-clock tests for omitted `now` in plan and verification paths.
- [ ] Replace service-local current-time acquisition with `context.clock` only where the service currently supplies a default; preserve explicit `now` arguments.
- [ ] Ensure finance/report windows still serialize with exact UTC boundaries across Amsterdam DST transitions.
- [ ] Run focused service, route, and reporting tests.
- [ ] Commit the injection slice.

### Task 3: Add typed API response models

**Files:** Create `ems/web/models.py`; modify `ems/web/routes/plan.py`, `verification.py`, `report.py`, `diagnostics.py`; tests under `ems/tests/`.

- [ ] Capture representative success, empty, stale, and unavailable payloads for each route.
- [ ] Define Pydantic models for plan, verification, report, finance, savings, diagnostics, and nested plan/verification items, retaining current JSON aliases and nullable fields.
- [ ] Add `response_model` declarations without changing service return dictionaries.
- [ ] Add contract tests that validate every representative payload through the declared model.
- [ ] Run focused route/model tests and Ruff.
- [ ] Commit the API contract slice.

### Task 4: Full verification and documentation

**Files:** Update the design/plan only if implementation decisions changed; add concise API-contract documentation if useful.

- [ ] Run `.venv/bin/pytest -q`.
- [ ] Run `.venv/bin/ruff check ems`.
- [ ] Run `npm run build` in `ems/web/frontend`.
- [ ] Run `git diff --check` and inspect `git status --short`.
- [ ] Record any deferred whole-repository clock work in the architecture backlog.

