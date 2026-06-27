# Built App Validation Handoff - 2026-06-27

## Scope

Validated the current built Energy Management System against `SPEC.md` as a runnable app and as an implementation of the broader energy-management spec.

The current build is a working mock/dry-run dashboard and API slice. It is not yet a complete live-control implementation of the spec. This handoff focuses on what to fix or add next, excluding prior review recommendations already being handled elsewhere.

## Validation Evidence

Commands run:

- `./.venv/bin/pytest` - passed: `103 passed, 1 warning in 0.66s`.
- `./.venv/bin/ruff check .` - passed: `All checks passed!`.
- `npm run build` in `ems/web/frontend` - passed. Vite built the SPA; main JS gzip size was about `47.72 kB`, below the spec's `300 KB` gzip budget.
- `npm run test:e2e` in `ems/web/frontend` - passed: `23 passed`.
- `docker compose -f docker-compose.dev.yml config` - passed; compose file renders successfully.
- Runtime smoke test with FastAPI serving the built SPA on `http://127.0.0.1:8098` - dashboard loaded, API calls returned 200, and desktop/mobile screenshots were inspected.

Runtime observations:

- Desktop screenshot: `ems-dashboard-validation.png`.
- Mobile screenshot: `ems-dashboard-mobile-validation.png`.
- Browser console had one error: `/favicon.ico` returned 404.
- All dashboard API requests observed during the browser run returned 200.

Note: `uv run pytest` and Playwright's default `uv run uvicorn ...` path hit sandbox/cache permission issues in this environment because `uv` wanted to use the user cache. The same tests passed through the local `.venv`, and Playwright passed when allowed to use its configured path. Consider setting `UV_CACHE_DIR` to a repo-local path in test scripts to make validation more hermetic.

## Iteration 1 - Runtime And Automated Validation

What is working:

- The backend starts and serves the React/Vite SPA.
- The dashboard renders useful dry-run telemetry: status metrics, current plan, prices, solar forecast, freshness, alerts, controller preview, battery mode, and savings estimate.
- The app has good initial test coverage for the current mock slice.
- The Docker dev compose file is syntactically valid and documents Mac/dev usage.
- Mac/container support is in the spec for dev/mock mode: `SPEC.md` section 11.6 explicitly says the app can run on macOS or any host, and `docker-compose.dev.yml` supports local Mac testing.

Bugs found:

1. Missing favicon causes a browser console error.
   - Evidence: browser run logged 404 for `/favicon.ico`.
   - Fix: add a small local favicon under the built SPA public assets or serve one from FastAPI.

2. Playwright uses `uv run` with the default uv cache, which is brittle in sandboxed/CI environments.
   - Evidence: `ems/web/frontend/playwright.config.ts:23` starts `uv run uvicorn ...`; sandbox validation failed until rerun with broader permission.
   - Fix: set `UV_CACHE_DIR=.uv-cache` in the Playwright `webServer.env`, or point Playwright at `.venv/bin/uvicorn` after dependency setup.

3. Mobile header wrapping is visually awkward.
   - Evidence: mobile screenshot shows `Smart Energy Manager` wrapping across multiple lines and `DRY-RUN` wrapping as `DRY-` / `RUN`.
   - Fix: make the topbar wrap intentionally, set `white-space: nowrap` on badges, and move badges to a second row on narrow screens.

4. Battery driver module comment contradicts the implementation.
   - Evidence: `ems/sources/battery.py:5-7` says `DISCHARGE_FOR_LOAD -> DISCHARGE`; `ems/sources/battery.py:22-32` correctly maps it to `AUTO` unless export discharge is explicitly allowed.
   - Fix: update the comment so the safety-critical behavior is not misdocumented.

5. `/health/ready` always reports ready in mock mode and does not expose degraded readiness semantics.
   - Evidence: `ems/web/api.py:89-91` returns only `{"status": "ready", "dry_run": ..., "dev_mode": ...}`.
   - Fix: once live adapters exist, readiness should prove config loaded, DB writable, required integrations reachable or explicitly degraded, and include clear degraded reasons.

## Iteration 2 - Spec Coverage Review

Major missing implementation items:

1. Live data and battery integrations are not implemented yet.
   - Evidence: `ems/main.py:28-42` always wires `MockSource`, `MockPriceSource`, `MockSolarForecastSource`, and `MockBatteryDriver`.
   - Missing: Home Assistant read client, Indevolt capability probe, HomeWizard meter ingestion, Tibber price adapter, Solcast/Forecast.Solar adapter, MQTT publishing, and real battery write adapter.

2. The planner does not yet answer the main energy-management question: when to charge and how much charge is needed based on expected daily solar production.
   - Evidence: `ems/planner/rule_based.py:1-6` states it is a simplified first cut; `ems/planner/rule_based.py:40-87` plans from prices only.
   - Missing: summer solar strategy, overnight energy need, target SoC, current SoC, load forecast, solar P10/P50/P90, battery capacity, charging power constraints, deadlines, reserve, and partial top-up logic.
   - Important user correction: the system should not try to keep the P1 meter at zero. Indevolt already does that. EMS should set high-level intent and calculate target charging needs.

3. The `Plan` shape is too small for the spec.
   - Evidence: `ems/planner/schedule.py:13-23` only stores slot start, intent, reason, and created time.
   - Missing: plan id/version, input snapshot, strategy, target SoC, target kWh, deadline, projected SoC curve, confidence, data quality, validator result, and economic assumptions.

4. There is no plan validator.
   - Spec requires validating slot coverage, min dwell, feasible charge windows, target SoC bounds, projected SoC, reserve, data quality, and remaining daily switch budget before applying a plan.
   - Current planner directly returns a plan without a validation step.

5. There is no live control loop that applies decisions.
   - Evidence: API uses `controller.preview(...)` only; `ems/web/api.py:133-154` is read-only. `ModeController.decide(...)` exists in `ems/control/mode_controller.py:95-115` but is not wired into a background control loop.
   - Missing: scheduled control loop, single writer ownership, persisted action history, missed-window recovery, shutdown safe-mode restore, and live/dry-run transition flow.

6. Controller runtime state is not persisted.
   - Evidence: `ems/control/mode_controller.py:7-8` says `switches_today` and `last_switch_at` are in-memory follow-up; fields are initialized in `ems/control/mode_controller.py:51-53`.
   - Risk: after restart, daily switch caps and dwell enforcement lose history.

7. Config loading only covers a small subset of the spec.
   - Evidence: `ems/config.py:10-17` only models timezone, dev mode, dry-run, web port, DB path, and cycle seconds.
   - Missing: battery, prices, solar, arbitrage, Home Assistant entity map, MQTT, auth, planner, ML, health, history retention, theme, and runtime settings overlay.

8. Runtime settings store is missing.
   - Evidence: `ems/storage/history.py:31-45` only creates raw and derived sample tables.
   - Missing: settings table, manual override with expiry, location/map pin, solar tilt/azimuth, reserve settings, planner mode, theme, guest mode, and effective config merging.

9. API surface is much smaller than `SPEC.md` section 9.1.
   - Evidence: `ems/web/api.py:85-244` implements status, freshness, prices, alerts, decision, battery, savings, plan, forecast, series, and JSON 404.
   - Missing: `/api/settings`, `/api/setup/checks`, `/api/export`, `/ws`, manual override/control endpoints, planner mode change, and authenticated settings writes.

10. WebSocket/live update path is missing.
    - Evidence: frontend polls 10 REST endpoints every 5 seconds in `ems/web/frontend/src/App.tsx:169-182`.
    - Missing: `/ws` stream for status/plan/freshness updates.

11. Price source is mock only.
    - Evidence: `ems/sources/prices.py:3-4` says the real Tibber adapter is future work.
    - Missing: Tibber API integration, cache, tomorrow availability, hourly-to-quarter-hour normalization, fallback behavior, staleness tracking, and price completeness validation.

12. Solar forecast source is mock only.
    - Evidence: `ems/sources/forecast.py:3-5` says real Solcast/Forecast.Solar adapters are future work.
    - Missing: API integration, free-tier budget tracking, forecast provenance, P10/P50/P90 handling from real data, correction factor, and remaining-day estimate.

13. Savings are illustrative, not measured.
    - Evidence: `ems/savings.py:1-5` says real savings will use measured energy later.
    - Missing: measured charge/discharge energy, baseline comparison, degradation cost accounting, realized vs projected savings, and confidence/explanation.

14. History storage is too narrow.
    - Evidence: `ems/storage/history.py:36-44` only creates `raw_samples` and `derived_samples`.
    - Missing: price cache, forecast cache, plan history, action decisions, runtime state, settings, alerts, exports, retention purge, vacuum, and backup support.

15. Security controls are not implemented.
    - Missing: LAN auth, same-origin/CSRF protection for future mutating endpoints, guest read-only mode, token redaction, debug export redaction, and reverse-proxy/TLS documentation hooks.

16. Setup wizard is missing.
    - Spec expects checks for P1 linked to Indevolt, battery reachable, HA token valid, Tibber token valid, forecast valid, and first-run dry-run summary.
    - Current UI is a single dashboard page.

17. Map/location setup is missing.
    - Spec allows OSM map tiles only on setup. Current frontend has no setup route, map pin, tilt/azimuth settings, or location editor.

18. ML mode/advisory surfaces are missing.
    - Spec has `rule_based | ml | advisory` planner mode and advisory plan diff. Current implementation has only the simplified rule-based planner.

19. MQTT/Home Assistant entity publishing is missing.
    - Spec expects status and alert entities, retained discovery topics, and HA select ownership synchronization.

20. Mac support is covered only for dev/mock.
    - Evidence: `SPEC.md:713-720`, `README.md:45-60`, and `docker-compose.dev.yml:1-8`.
    - Recommendation: keep this distinction explicit everywhere. Do not imply Mac Docker mode can control a live battery unless live integrations, networking, secrets, auth, and safety checks are implemented and tested for that deployment.

## Iteration 3 - UI, UX, And Visual Quality Review

What is good:

- The dashboard is compact and operational rather than marketing-like.
- It exposes the most important current mock signals without unnecessary decoration.
- It uses a stable, readable layout on desktop and mostly works on mobile.
- Dry-run state is visible.

UI bugs and beauty recommendations:

1. Fix narrow-screen topbar wrapping.
   - Add a responsive header layout with title on the first row and status badges on a second row below about 520px.
   - Add `white-space: nowrap` to `.badge`.

2. Reduce card radius or define a design-system exception.
   - Evidence: cards use `border-radius: 14px` in `ems/web/frontend/src/styles.css:79` and `:90`.
   - Recommendation: use 8px for operational dashboard cards unless a design system intentionally says otherwise.

3. Avoid the one-note dark slate/green/amber palette.
   - Current palette in `ems/web/frontend/src/styles.css:1-10` is usable but visually narrow.
   - Add light/dark theme support from `web.theme`, use a neutral operational base, and reserve green/amber/red strictly for status meaning.

4. Make charts accessible.
   - Evidence: plan, price, and forecast visual bars are `aria-hidden` in `ems/web/frontend/src/App.tsx:83`, `:110`, and `:135`.
   - Add accessible summaries: min/current/max price, cheapest window, peak window, forecast total, confidence, and current plan action.

5. Add richer operational explanations.
   - The controller panel is useful, but the spec expects "why not charging?" explanations.
   - Add deterministic precondition checks: grid charging disabled, no cheap slot, target reachable by solar, price stale, forecast stale, startup grace, switch cap reached, dwell not elapsed, P1 not paired.

6. Add explicit mock/simulation affordance.
   - Current badges show `DRY-RUN`, `source: mock`, and `complete`; this can read as more production-ready than it is.
   - In mock mode, use "Simulation" or "Mock data" in the main status band and keep data quality separate from source realism.

7. Add setup and settings navigation.
   - The current UI is one dashboard. Spec needs setup, settings, manual override, planner mode, export, and diagnostics.
   - Keep it operational: tabs or a left rail, not a marketing landing page.

8. Add manual override controls with expiry.
   - Required controls: force AUTO for 6h, force hold/reserve until time, clear override.
   - Must persist through runtime settings and be visible in both UI and HA surface.

9. Improve plan timeline usefulness.
   - Current timeline is compact but hard to inspect.
   - Add hover/focus tooltips, a legend, selected slot details, target SoC, expected kWh, and price/solar context for each planned action.

10. Add visual regression tests.
    - Current e2e tests assert presence/content. Add screenshot baselines for desktop and mobile states, including loading, error, degraded data, dry-run, and live-control disabled.

## Prioritized Fix List

### P0 - Required Before Real Control

1. Implement live adapters and capability probe: HA client, Indevolt probe, HomeWizard meters, Tibber prices, solar forecast source.
2. Implement the real energy planner around target charging need:
   - expected overnight load,
   - current SoC,
   - usable capacity,
   - reserve,
   - expected solar production,
   - P10 safety case,
   - target SoC/kWh,
   - deadline,
   - charging feasibility.
3. Add a plan validator and reject unsafe/infeasible plans.
4. Add persisted runtime state for switch caps, dwell, last action, ownership, original vendor mode, and manual override.
5. Add a live control loop that is the only writer and applies at most one command per cycle.
6. Add authenticated settings/control APIs before any mutating UI is exposed.
7. Add shutdown safe-mode restore and live readiness/degraded health checks.

### P1 - Required For A Complete Spec-Conformant App

1. Expand `Config` and implement runtime settings overlay.
2. Add setup wizard and validation checks.
3. Add `/ws`, `/api/settings`, `/api/setup/checks`, `/api/export`, and manual override/control APIs.
4. Implement MQTT/HA entity publishing and retained discovery.
5. Implement price/forecast caching, staleness, and fallback rules.
6. Store plan history, action decisions, prices, forecasts, settings, and alerts.
7. Implement savings from measured energy and show projected vs realized savings.
8. Add security controls: auth, CSRF/same-origin, redaction, guest read-only.

### P2 - Polish And Product Quality

1. Add favicon.
2. Fix mobile topbar wrapping.
3. Add light/dark theme support.
4. Refine visual system: smaller radii, clearer status color semantics, better hierarchy.
5. Add accessible chart summaries and keyboard/focus support.
6. Add screenshot/visual regression tests.
7. Make test commands hermetic with repo-local uv cache.
8. Document Mac Docker as dev/mock only in every operational guide.

## Suggested Next Development Slice

Implement the "daily charge need" planner slice before expanding the UI further:

1. Extend planner inputs with current SoC, usable capacity, reserve, load forecast, solar forecast P10/P50, max charge power, and price slots.
2. Produce a plan with `target_soc`, `target_kwh`, `deadline`, `projected_soc`, `strategy`, `data_quality`, and `input_snapshot`.
3. Add plan validation.
4. Surface the target and reason in the dashboard.
5. Keep `DISCHARGE_FOR_LOAD` mapped to vendor self-consumption/AUTO by default; do not implement P1-zeroing logic in EMS.

This directly addresses the core purpose of the spec: determine whether charging is needed, how much is needed, and when to charge, while letting Indevolt continue to handle instantaneous P1 zeroing.
