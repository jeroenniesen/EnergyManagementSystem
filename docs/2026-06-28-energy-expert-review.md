# Energy Expert Review - 2026-06-28

## Scope

I reviewed the current spec, supporting docs, and application as an energy-management system, not just as software. The focus was: does the app make the right energy decisions, does it respect the Indevolt/P1 boundary, and what would I fix before trusting it with live battery control?

This is based on the current worktree on 2026-06-28. The application has advanced materially since the earlier mock-only validation: it now includes live sensing, settings, an adaptive planner, projection, control loop, audit, diagnostics, AI explanation surfaces, and many more tests.

## Verification Run

Checks I ran:

- `./.venv/bin/pytest` - passed: `358 passed, 1 warning`.
- `./.venv/bin/ruff check .` - passed.
- `npm run build` - passed; built JS gzip size about `60.57 kB`.
- `npm run test:e2e` - failed: `46 passed, 8 failed`.

The e2e failure is not a simple UI defect. Playwright starts the app against the repo DB/settings, and the app picked up persisted live-device and AI settings. The tests expected mock mode, but the app booted `dev_mode: live`, called LAN Indevolt IPs, and used an enabled MiniMax configuration that returned `402 Payment Required`. That is a serious test isolation and commissioning issue: tests must not depend on the operator's persisted runtime settings or real devices.

## Loop 1 - Spec And Energy Model Review

### What Is Strong

- The spec now has the right cardinal rule: Indevolt owns P1 zeroing; EMS should not run a tight power-control loop.
- `docs/control-model.md` correctly frames the EMS as an intent/mode switcher.
- The energy model correctly says P1 is net grid flow, not house load. Reconstructing `house_load = grid + solar + battery` is the right foundation.
- The docs separate EV load from non-EV house load. That matters: the home battery should not silently feed a 10 kW car session.
- The target-SoC math in the docs is directionally right: compute need in kWh, convert to target SoC, charge only the shortfall, respect reserve and deadlines.
- The backtest in `docs/charging-algorithm-research.md` is a good sign. Comparing adaptive planning to a DP optimum is exactly the sort of validation this system needs.

### Spec Gaps

1. **The tariff/economic model is still too simplified.**
   The spec handles spot price, round-trip efficiency, degradation, import fees, and export value, but the application needs a first-class tariff model for the Netherlands: all-in import price, feed-in compensation, export penalties/terugleverkosten, taxes, fixed fees excluded from marginal decisions, and negative prices. If this is wrong, the optimizer will make rational-looking but financially wrong choices.

2. **The spec should explicitly model "solar curtailment / export avoidance".**
   Current language focuses on filling the battery and arbitrage. In practice, once net metering economics worsen, the battery's value is often avoiding low-value or penalized export. The planner should rank "store surplus that would otherwise be exported cheaply" separately from "buy grid energy cheaply."

3. **Strategy selection by month is too crude.**
   `auto = summer by April-September` is a useful fallback, but the real strategy should be energy-condition driven: forecast surplus, expected overnight need, current SoC, and price spread. A sunny March day and a dark September day should not behave merely by calendar.

4. **Plan validity should be a hard gate in the spec and code.**
   The docs describe a validator, but the current domain object cannot express enough fields for it. Before live control, a plan needs target SoC, projected SoC, switch count, data-quality, input snapshot, and deadline.

5. **EV treatment is still under-specified.**
   "Read-only EV via HomeWizard car meter" is right for v1, but the spec should state how EV charging is forecast: known schedule, recent pattern, manually entered session, or "unknown load, do not reserve battery for it." Without that, a large EV session can distort load learning and battery planning.

## Loop 2 - Application And Implementation Review

### What Is Strong

- The current app is no longer just a mock dashboard. It has live HomeWizard/Indevolt/Tibber paths, settings store, diagnostics, audit, override, projection, energy-story UI, and many tests.
- The adaptive planner is a meaningful improvement over fixed night targets. It sizes to forecast deficit and nets conservative solar.
- The car guard is the right idea: while the car is charging, avoid discharging the home battery into it.
- The live driver is gated by default, and `dry_run` remains true unless operational mode is explicitly enabled.
- The projection uses one-way efficiency via `sqrt(round_trip_efficiency)`, which is the right way to avoid hiding losses.

### What Is Completely Wrong Or Unsafe

1. **Live wiring can substitute the P1 meter as solar or EV meter.**
   In `ems/connection.py:132-135`, if `meters.solar_ip` or `meters.car_ip` is missing, the app uses the P1 IP for those roles. That is wrong for energy accounting. P1 is net grid flow; it is not PV production and not EV load. This can corrupt reconstructed load, EV guard behavior, forecast learning, and planner inputs. Missing solar/car meters should be marked missing/degraded, never replaced with P1.

2. **The plan cannot carry the amount to charge.**
   `ems/planner/schedule.py:13-23` only stores start, intent, and reason. The app can talk about target SoC elsewhere, but the actual plan handed to the controller cannot carry `target_soc`, `target_kwh`, `deadline`, `power_w`, or floor. This is the central abstraction gap. A battery-control app that cannot pass target SoC through the plan is not ready for live charging.

3. **The live Indevolt driver defaults CHARGE/DISCHARGE to target SoC 100.**
   `ems/sources/indevolt_driver.py:68-81` has `target_soc: int = 100`, and `apply()` calls it without a target at `ems/sources/indevolt_driver.py:139-147`. If operational control is armed before target SoC is wired through, a charge intent can become "charge to 100%" rather than "charge the calculated shortfall." That contradicts the energy goal.

4. **Operational mode is a settings toggle, but the system is not commission-proof.**
   `ems/connection.py:118-141` arms the driver and lifts dry-run when `control.operational` is true and an Indevolt IP exists. That should require a commissioning checklist: validated meter roles, P1 pairing, capability probe, target-SoC plumbing, export policy, restore mode, and a successful dry-run acceptance period.

5. **Manual override can bypass data-quality fail-safe.**
   `_effective_intent()` says an active override wins over the plan and fail-safe at `ems/web/api.py:490-515`. I would not allow arbitrary unsafe overrides. Force AUTO is fine. Force charge/discharge/hold should still require battery reachability, sane SoC, valid meter roles, and command safety.

6. **Switch counter and last-switch state are still in memory.**
   `ems/control/mode_controller.py:7-8` documents this. The fields are initialized at `ems/control/mode_controller.py:51-53`. A restart resets dwell and daily switch protection, which is exactly when systems are most likely to do surprising things.

7. **Readiness is too optimistic.**
   `/health/ready` returns ready regardless of whether live devices, DB, forecast, prices, or battery are usable (`ems/web/api.py:627`). For a control system, readiness should distinguish "process up", "dashboard usable", "sensing degraded", and "safe to control."

8. **E2E tests are not hermetic.**
   `ems/web/frontend/playwright.config.ts:23-32` starts the real app, which reads persisted runtime settings. The failed e2e run booted live mode and talked to real LAN devices. Tests must force mock/replay DB and disable AI/network/live devices.

9. **Winter planner is still price-only.**
   `plan_rule_based()` ignores SoC, forecast, expected load, target SoC, EV load, and deadlines. For winter arbitrage it should size energy to expensive load windows, not just mark cheapest and priciest slots.

10. **The live battery endpoint does not expose the real driver capabilities cleanly.**
    In live mode, `battery_endpoint` is `None`, so `/api/battery` can show towers but not current mode/capabilities from the same driver path that control uses. The UI and diagnostics should see the same capability report the controller uses.

## Loop 3 - Synthesis And Improvement Backlog

### Expert Opinion

The project is moving in the right direction. The best parts are the energy-model thinking, the refusal to fight P1 zeroing, the car guard, the adaptive planner research, and the growing test suite.

My honest concern is that the system is starting to expose "operational" controls before the core control contract is complete. The planner still emits a coarse mode schedule, while the actual battery write API needs precise target SoC and power. That gap is not cosmetic. It is the difference between "top up 2.3 kWh before sunset" and "charge to 100% because the driver default said so."

I would pause live-write ambitions and harden the energy contract first: exact meter roles, target-carrying plan slots, validator, persisted runtime state, and commissioning gates.

### Improvement Items

#### P0 - Do Before Any Live Battery Writes

1. Replace the `Plan` domain object with a real energy-control plan:
   - `id`, `version`, `strategy`, `created_at`
   - slots with `start`, `end`, `intent`, `target_soc`, `target_kwh`, `power_w`, `floor_soc`, `deadline`, `reason`
   - `input_snapshot`, `projected_soc`, `data_quality`, `validator_result`

2. Wire target SoC end to end:
   - planner computes target,
   - plan stores target,
   - controller passes target,
   - driver writes target,
   - post-write confirmation verifies mode and target.

3. Remove P1 fallback for missing solar/car meters.
   Missing solar or EV meter should degrade the relevant feature. P1 must never impersonate another meter.

4. Add a hard plan validator.
   Reject plans with impossible charging, missing target, stale inputs, invalid SoC projection, excessive switch count, sub-dwell slot churn, or missing battery capability.

5. Make operational mode a commissioning flow, not a normal toggle.
   Require explicit checklist acceptance, dry-run evidence, current capability report, live meter validation, and target-SoC plumbing before arming.

6. Persist runtime control state.
   Store switches today, last switch time, last requested action, last confirmed action, original vendor mode, unresolved warnings, and active override.

7. Restrict manual override semantics.
   Always allow "return to AUTO". Do not allow force charge/discharge if data quality is unsafe, battery is unreachable, SoC is stale, or capability probe is invalid.

8. Fix readiness semantics.
   Add separate statuses for `alive`, `dashboard_ready`, `sensing_ready`, `planning_ready`, and `control_ready`.

#### P1 - Make The Energy Logic Better

1. Replace month-based auto strategy with an energy-state selector.
   Choose based on solar surplus forecast, price spread, current SoC, expected load, and export economics.

2. Upgrade winter arbitrage to demand-sized planning.
   Calculate expensive-window load, existing SoC above reserve, required grid top-up, target SoC, and deadline before the first peak.

3. Make tariff economics explicit.
   Add import marginal price, export value, export fees/penalties, taxes, VAT, fixed costs excluded from marginal decisions, degradation, and negative-price policy.

4. Model export avoidance as a first-class objective.
   Score battery charging from solar surplus differently from grid charging. Avoid exporting when export value is low or negative.

5. Add forecast confidence and error learning.
   Track forecast-vs-actual solar by time of day and weather regime. Use that to adjust P10/P50 rather than relying only on provider percentiles.

6. Improve load forecasting.
   Current hourly average is a good baseline. Add weekday/weekend, recent-day weighting, holiday/away exclusion, and unusual-day filters. Keep EV excluded.

7. Add terminal SoC value to optimization/backtests.
   The DP module notes that a pure cost objective may drain to reserve at horizon end. Add terminal value or rolling-horizon tests for all candidate planners.

8. Add an EV session model.
   Read actual car load from HomeWizard, but also let the user enter expected charging sessions. The battery should not reserve energy for the car unless explicitly configured.

#### P2 - Product, Testing, And UI Quality

1. Make e2e tests hermetic.
   Use a temporary DB/settings store, force mock devices/prices/AI, and never read the operator's persisted settings.

2. Add live-read integration tests with fixtures.
   Keep hardware out of CI, but test recorded HomeWizard, Tibber, Forecast.Solar, and Indevolt payloads through the same adapters.

3. Add commissioning UI.
   Show meter role validation, sign checks, P1 pairing, capability report, dry-run period summary, and "safe to control" status.

4. Make the UI show "amount of energy" everywhere a mode is shown.
   Users should see: charge X kWh, target Y%, by Z time, because solar shortfall is Q kWh.

5. Improve alert hierarchy.
   Separate info, degraded, unsafe, and control-blocking alerts. A stale optional feature should not look the same as an unsafe battery input.

6. Add plan replay/export.
   Export the exact inputs, plan, projection, and decision history so every surprising decision can be reproduced.

7. Keep AI advisory only.
   AI explanation is fine as a UI layer. It should never validate safety, decide actions, or block fallback.

## Top 10 Improvements

1. **Add target SoC/kWh/deadline to `PlanSlot` and wire it through to the Indevolt driver.**
2. **Remove P1-as-solar/car fallback; missing meters must degrade, not impersonate.**
3. **Block operational mode until a commissioning checklist passes.**
4. **Change the live driver so CHARGE never defaults to target SoC 100.**
5. **Persist switch counters, dwell state, last action, original vendor mode, and override state.**
6. **Implement a hard plan validator before any control write.**
7. **Make e2e tests use an isolated mock DB/settings store.**
8. **Replace winter price-only planning with demand-sized target-SoC planning.**
9. **Add an explicit Dutch tariff/export economics model.**
10. **Make readiness and diagnostics distinguish dashboard-ready from control-ready.**

## Bottom Line

The app is promising and much better than a simple price-slot automator. The energy model is mostly pointed in the right direction. The biggest problem is that the implementation still treats "mode" as the control payload, while the real energy problem is "mode plus exact target, amount, deadline, and validation." Fix that abstraction before arming live writes.
