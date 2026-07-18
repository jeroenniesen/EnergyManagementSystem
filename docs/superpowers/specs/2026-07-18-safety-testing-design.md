# Design — Safety invariants, fault injection, accessibility gate

*Brainstormed 2026-07-18. Owner: Jeroen. Feeds a `writing-plans` implementation plan.*
*Backlog: B-84 (safety invariants), B-81 (fault injection), B-82 (accessibility) — all E-09 P1.*

## 1. Goal & context

Three P0 quality gaps identified during QA audit: safety-critical guarantees have no
automated enforcement, fault scenarios are untested, and the CI pipeline has no
accessibility gate despite SPEC §9.1 requiring WCAG 2.1 AA.

This design covers **three independent deliverables** that share a theme (proving the
system is safe and accessible) but are structurally separate:

1. **Safety invariant tests** — scenario-based proof of the "never worse than no EMS"
   guarantees (reserve floor, single writer, AUTO fallback). New file.
2. **Fault injection tests** — destructive scenarios (battery timeout, malformed prices,
   process restart) run locally with `pytest -m fault_injection`. New file.
3. **Accessibility gate** — axe-core checks in existing Playwright e2e specs, enforced
   in CI. Modification of 9 files + CI config.

Each is independently valuable and can ship separately. All three together address the
P0/P1 quality gaps from the QA audit.

### Hard invariants to preserve

1. **No behavior change to control logic.** Tests observe; they never modify the
   decision paths. If a test reveals a gap, we fix the *test coverage*, not relax
   the guarantee.
2. **Single battery writer.** All tests route through `ModeController.decide()` →
   `driver.apply()`. No test bypasses the controller.
3. **Fail-safe to AUTO.** Every failure path must end in `ALLOW_SELF_CONSUMPTION` /
   `PhysicalMode.AUTO`. Tests verify this explicitly.
4. **Dry-run respected.** Fault injection tests that involve writes use `dry_run=False`
   with a mock driver — never touch real hardware.

## 2. Safety invariant tests (B-84)

**What:** Scenario-based tests proving the EMS enforces its safety constraints under
adversarial conditions.

**Why:** These are the guarantees behind "never worse than no EMS." Currently enforced by
code review and manual testing only. If a regression breaks the reserve floor, no test
catches it until someone's battery is drained.

**File:** `ems/tests/test_safety_invariants.py` — self-contained, uses existing test
infrastructure (MockBatteryDriver, ControlService with injected callables).

### Scenarios

Each test constructs a `ControlService` with mock collaborators and injected callables,
then drives it through the scenario. Pattern follows `test_control_service.py` (already
established).

| # | Scenario | Setup | Expected outcome |
|---|----------|-------|-----------------|
| 1 | **Reserve floor respected — planner discharge** | SoC at reserve (20%), plan slot is DISCHARGE_FOR_LOAD, data quality "complete" | Validator rejects (finding severity=unsafe), effective_intent is ALLOW_SELF_CONSUMPTION, no battery write |
| 2 | **Reserve floor respected — override discharge** | SoC at reserve, manual override is DISCHARGE_FOR_LOAD | Override intent gated to HOLD_RESERVE (car-guard or validator), battery NOT discharged below reserve |
| 3 | **AUTO fallback on unsafe data** | Plan is GRID_CHARGE_TO_TARGET, `data_quality` returns "unsafe" | effective_intent is ALLOW_SELF_CONSUMPTION with fail-safe reason, physical mode AUTO |
| 4 | **AUTO fallback on write failure** | FailingMockBatteryDriver(fail_times=2), plan is GRID_CHARGE_TO_TARGET | After retry, controller falls back to AUTO; no repeated writes on same cycle |
| 5 | **Single-writer: concurrent cycles serialised** | Two `run_cycle()` coroutines started simultaneously via `asyncio.gather` | Only one battery write executes; the other waits on `control_lock` and does NOT issue a second write |
| 6 | **No command on failed validation** | `validate_plan_obj` returns PlanValidation with unsafe finding | effective_intent is ALLOW_SELF_CONSUMPTION, no battery command issued |

### Design decisions

- **Scenario-based, not property-based.** Each test names the invariant it proves. A
  human reading the file understands what's being tested without decoding hypothesis
  strategies. Simpler to maintain.
- **Uses existing mock infrastructure.** `MockBatteryDriver`, `FailingMockBatteryDriver`,
  injected callables — same pattern as `test_control_service.py`. No new test doubles.
- **Tests the decision path, not the planner.** The planner is already tested in
  `test_planner.py`. These tests verify the *guards* between planner output and battery
  write: validation, failsafe gate, car guard, single-writer lock.

## 3. Fault injection tests (B-81)

**What:** Destructive failure scenarios that verify the EMS survives and recovers from
realistic hardware/infrastructure faults.

**Why:** These are the failure modes that matter most in a real home — the battery
becomes unreachable, price data is corrupted, or the process restarts mid-operation.
Without testing them, we're flying blind on recovery paths.

**File:** `ems/tests/test_fault_injection.py` — all tests marked
`@pytest.mark.fault_injection`, skipped in CI (the existing `ci.yml` backend job runs
`uv run pytest ems/tests` without `-m`).

**Local execution:** `pytest -m fault_injection` or a new Makefile target
`make fault-injection`.

### Scenarios

| # | Scenario | Fault injection method | Expected outcome |
|---|----------|----------------------|-----------------|
| 1 | **Battery timeout during control cycle** | `source.read()` raises `TimeoutError` on first call, succeeds on second | `control_tick` catches via coalesced read fail-safe; keeps last-good sample; logs "live sample read failed"; next cycle recovers with fresh data |
| 2 | **Malformed price forecast** | `price_source.slots()` returns `[None, "garbage"]` or empty list | Strategy resolution catches exception in `strategy_inputs()`, surplus/spread are None; falls back to season-based strategy; no crash |
| 3 | **Process restart mid-lease** | `control_state` DB shows `OwnershipState.CONTROLLING` from previous run; simulate via direct DB write | Lifecycle recovery: on startup, if state is CONTROLLING but no recent tick (grace expired), resets to PROBE; battery probed fresh, not assumed in known mode |

### Design decisions

- **`@pytest.mark.fault_injection` marker.** Colocated with the test suite (clean),
  skipped in CI by default. A future `make fault-injection` target or manual invocation
  runs them. This avoids duplicating the test directory structure.
- **Faults injected at the seam level.** We don't mock network sockets or kill processes.
  Instead we inject faults through the existing callable/driver interfaces — same pattern
  as `FailingMockBatteryDriver`. This keeps tests deterministic and fast.
- **Each test is a complete cycle.** Setup → inject fault → run `control_tick` or
  `run_cycle` → verify recovery. No shared state between tests.

## 4. Accessibility gate (B-82)

**What:** Automated WCAG 2.1 AA checks in the Playwright e2e suite, enforced as a CI gate.

**Why:** SPEC §9.1 requires WCAG 2.1 AA compliance. Currently no automated check exists —
the only reference is one comment in `insights.spec.ts`. This closes the gap.

**Implementation:** Add `@axe-core/playwright` to the frontend devDependencies and call
`await accessibility.check(page)` at the end of each e2e test.

### Approach

**Per-test check:** Each existing `test()` block gets a single assertion at the end:

```typescript
await expect(await accessibility.check(page)).toHaveNoViolations();
```

This is the axe-core Playwright integration — it runs axe against the current page DOM
and reports WCAG violations. It's what the Playwright docs recommend for a11y testing.

### Which tests get it

All 9 existing e2e specs, but with targeted scopes:

| Spec | Tests with a11y check | Rationale |
|------|----------------------|-----------|
| `ui.spec.ts` (82 tests) | All dashboard/insights/manage views | Primary user-facing surfaces — highest priority |
| `settings.spec.ts` (19 tests) | All settings interactions | Form fields, labels, focus management are a11y-critical |
| `car.spec.ts` (13 tests) | All car page views | Consumer-facing surface |
| `insights.spec.ts` (43 tests) | All insights views | Data visualization alternatives, chart text |
| `manage.spec.ts` (11 tests) | System/manage views | Operational surfaces |
| `auth.spec.ts` (2 tests) | Login/auth flows | Form accessibility is WCAG-critical |
| `override.spec.ts` (2 tests) | Override controls | Safety-critical UI must be accessible |
| `api.spec.ts` (16 tests) | Skipped — API-only, no DOM | N/A |
| `theme.spec.ts` (2 tests) | Light/dark theme contrast checks | Directly relevant to WCAG color contrast |

### Exclusions & pragmatism

- **Demo/empty states** are exempt — they're intentionally incomplete placeholders.
  A11y checks run only on populated views (the existing tests already seed data).
- **Violations that are genuinely N/A** (e.g., a chart image without alt text because
  the adjacent stat tile provides the equivalent) are suppressed with `axe-core` ruleset
  configuration, not ignored. We configure the ruleset once in `playwright.config.ts`.
- **Initial run may find violations.** The first CI run will likely fail. That's the
  point — it surfaces what needs fixing. We fix the violations in a follow-up PR, not
  suppress them.

### CI integration

One-line change to `ci.yml` frontend job: add `@axe-core/playwright` to the
`npm ci` install (or a separate `npm install -D @axe-core/playwright` step). The
Playwright e2e step already exists; the a11y assertions ride the same step. A failing
a11y check blocks the merge, same as any other test failure.

First CI run will likely fail (surface existing violations). Those fixes are a follow-up
PR, not part of this implementation.

## 5. Out of scope (explicit)

- **Property-based testing** — scenario-based is sufficient for the invariants we need
  to prove; hypothesis adds complexity without proportional value here.
- **Prometheus/OpenTelemetry** — performance budgets (B-80) and fault injection are
  local/CI concerns; external monitoring is a separate architectural decision.
- **Visual regression enforcement** — SPEC §14 screenshot gates are deferred to E-10's
  UI redesign (B-87). Current `theme.spec.ts` coverage is adequate for now.
- **iOS a11y** — the iOS app has its own test suite; XCUI testing for accessibility is
  a separate effort.

## 6. Execution order

1. Safety invariant tests (new file, ~25 mins to write)
2. Fault injection tests (new file, ~30 mins to write)
3. Accessibility gate (9 spec files + 1 config, ~45 mins to wire)

Items 1 and 2 are independent and can be written in parallel. Item 3 touches the
frontend and is naturally sequential (add dependency → modify specs → verify CI).
