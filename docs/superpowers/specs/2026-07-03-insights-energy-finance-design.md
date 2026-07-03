# Insights energy graphs + daily finance history — design spec

*2026-07-03. Goal-driven (user): (A) Insights graphs of how P1 (grid), House and Car energy behaved;
(B) persistent per-day financial history — grid cost, battery cost, money saved. Read-only features:
no control path, no battery writes. Delivers part of backlog B-03 (measured savings) and B-13
(rollups that outlive raw retention).*

## A — Energy-behavior series (graphs)

**Backend.** `build_series(raw_rows, derived_rows, *, period, start, end, tz)` (pure, in
`ems/reporting.py`), returned as a new `series` field on the existing `/api/report` response (the
Insights view already fetches it per window; no new polling).

- Buckets: `day` → 15-min slots; `week`/`month` → local days; `year` → local months.
- Per bucket: `{start, grid_import_kwh, grid_export_kwh, house_kwh, car_kwh, solar_kwh}`.
- Math mirrors `energy_flow`: floor samples to 15-min slots, mean W per slot → kWh, sum slots into
  the bucket (local-tz day/month boundaries). Row volume follows the existing window-sized `limit`
  pattern in `/api/report`.

**Frontend.** New `EnergyBehavior` section in Insights: day = stepped power curves (house / car /
grid ± with export below zero); week/month/year = per-bucket bars. Hand-rolled SVG in the house
style, theme tokens for colors, SR summaries like the other charts.

## B — Daily finance history

**Price persistence (prerequisite).** New `price_slots` table (`start_ts` PK, `eur_per_kwh`),
upserted best-effort by the `Recorder` each cycle from `price_source.slots()` (idempotent, ~96
rows/day, in-thread like source reads; a failure never kills the cycle). Purged with the same
retention as raw samples. Days before this ships have no prices — finance reports that honestly
(`price_coverage`), never invents numbers.

**Finance math** (`ems/finance.py`, pure). Per local day, from raw rows + price slots:
- Slot basis: 15-min mean `grid_w`, `battery_w`; import/export split by sign; slot price by lookup.
- `grid_cost_eur` = Σ import·price − Σ export·price (export credited at spot — saldering holds
  until 2027; revisit with B-05).
- `battery_cost_eur` = battery discharge kWh × `planner.degradation_eur_per_kwh` (wear on
  delivered energy).
- Baseline (counterfactual "no battery, same solar + loads"): per slot `grid'_w = grid_w +
  battery_w` (removing the battery from the meter balance), costed the same way.
- `saved_eur` = baseline grid cost − actual grid cost − battery wear.
- `price_coverage` = share of slots-with-samples that had a price; totals are `None` when 0.

**Rollup storage.** `daily_finance` table keyed by local day, written lazily: serving a completed
day computes-and-stores once; the running day is always computed fresh and never stored. **Never
purged** — this is the long-horizon financial record (B-13).

**API.** `GET /api/finance?period=day|week|month|year&date=` → `{window, days: [DayFinance…],
totals}`. Reuses `resolve_window`. Effective settings supply degradation.

**Frontend.** "Money" section in Insights: totals (saved / grid cost / battery wear), per-day saved
bars for week/month/year, coverage caveat when price history is incomplete.

## Testing
- `test_finance.py`: exact € on canned slots (import/export/battery), baseline identity, missing
  prices → coverage + None totals, empty day.
- `test_history.py` additions: price upsert idempotency, `prices_between`, `daily_finance`
  roundtrip, purge keeps `daily_finance` but trims `price_slots`.
- `test_sense.py` addition: recorder upserts prices per cycle; price failure doesn't kill the cycle.
- `test_reporting.py` additions: series bucketing per period (day slot, week day-buckets, year
  month-buckets, tz boundaries).
- API tests: `/api/finance` day + week (lazy rollup persisted), `/api/report` carries `series`.
- Playwright: Insights shows the behavior chart + money section against a seeded DB.

## Non-goals
No dynamic-contract fee modelling (fixed fees/taxes cancel in saved-€ deltas at spot; document),
no gas costs (B-02 lands gas), no export-tariff asymmetry until B-05, no schema change to existing
tables.
