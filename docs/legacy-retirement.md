# Legacy & compatibility retirement register

> **B-85 / [#83](https://github.com/jeroenniesen/EnergyManagementSystem/issues/83).**  
> Machine-readable source of truth: [`ems/legacy_registry.py`](../ems/legacy_registry.py)  
> (`LEGACY_REGISTRY`). This page is the human companion — edit the Python module first, then
> keep this summary in sync.

## Rules

1. **Do not delete a retained path** until its `removal_trigger` is met (and preferably logged in
   the PR that removes it).
2. **Do not add a new compatibility shim** without a new `LegacyEntry` (owner, reason, usage
   signal, removal trigger).
3. **Removed** entries stay in the register so CI can assert the path stays gone.
4. **Ignored** entries are scratch/temp artifacts — covered by `.gitignore`, never committed.

## Register (summary)

| Id | Path / surface | Status | Owner | Removal trigger (short) |
|---|---|---|---|---|
| `forecast-snapshots-table` | `forecast_snapshots` table + HistoryStore helpers | retained | storage / replay | Full retention window ledger-only; no replay fallback hits |
| `forecast-snapshots-read-fallback` | `ems/replay.py` day-ahead fallback | retained | replay / insights | With table drop |
| `forecast-snapshots-upsert-helper` | `upsert_forecast_snapshot` | retained | storage | With table drop (tests seed ledger only) |
| `ev-quick-advice-endpoint` | `GET /api/advisor/ev-charge` | retained | web / car | After deprecation window; SPA already unused |
| `ev-departure-time-setting` | `ev.departure_time` | retained | settings / car | With quick-advice endpoint |
| `ev-advisor-module` | `ems/ev_advisor.py` | retained | car | With quick-advice endpoint |
| `sim-backtest-harness` | `ems/sim.py` | retained | planner research | Only if replay fixtures replace `test_sim` claims |
| `planner-optimal-dp` | `ems/planner/optimal.py` | **removed** | planner | Stay gone (B-27 / #76) |
| `price-includes-all-fees-alias` | `grid_fees.tibber_total_includes_all` | retained | prices / settings | After upgrade window; canonical key only |
| `legacy-shared-token-tier-cap` | `authz._LEGACY_ACCESS_CAP` | retained | auth | Every access token has an explicit tier |
| `planner-mode-ml-advisory` | `PlannerMode` + `planner.mode` ml/advisory | retained | planner / ml | Enable at M6 — do not delete the seam |
| `control-commitment-reserve-setting` | `control.commitment_reserve` | retained | control / settings | Remove after upgrade window; use `max_commitment_switches_per_day` (#309) |
| `root-validation-screenshots` | root `*-validation.png` / `behavior-panels.png` | ignored | repo hygiene | `.gitignore` |
| `root-task-reports` | root `task-*-report.md` | ignored | repo hygiene | `.gitignore` |

Full prose for each entry (reason + usage signal) lives in `ems/legacy_registry.py`.

## Themes

### Forecast snapshots → prediction ledger

Live sensing writes **only** to `forecast_ledger`. The old `forecast_snapshots` table remains for
historic rows and as a **read-only** replay fallback. Do not revive writes from `sense` /
`Recorder`.

### EV quick advice → car plan

`GET /api/car/plan` is the supported schedule-aware advisor. `/api/advisor/ev-charge` and
`ev.departure_time` are compatibility-only. `ev.charge_kwh` stays — winter EV-day sizing (#181)
still uses it.

### Research simulation

`ems/sim.py` is offline backtest evidence for the adaptive summer charger. Former
`ems/planner/optimal.py` (DP yardstick) is **already removed**; numbers remain in
[`charging-algorithm-research.md`](./charging-algorithm-research.md).

### Temporary artifacts

Root validation PNGs and `task-*-report.md` files are gitignored. Prefer writing agent scratch
under ignored dirs (`.e2e-data/`, `/tmp`, …), not the repo root.

## Related backlog

- **B-27** — dead planner logic (`optimal.py` already gone; charge-kind dedupe may remain).
- **B-54** — broader repo/test hygiene (iOS build trees, package layout).
- **M6** — when `planner.mode` ml/advisory gain real adapters, update
  `planner-mode-ml-advisory` (clear `disabled_options`, do not invent a second enum).
