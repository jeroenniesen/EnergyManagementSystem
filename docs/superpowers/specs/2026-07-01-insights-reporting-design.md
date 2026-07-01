# Insights & Reporting — design spec

*Brainstormed 2026-07-01. Status: draft for review. A read-only reporting feature — no control path, no
battery writes.*

## Goal

An **Insights** section that (in priority order) **(A) motivates** — 0–100 grades the household can watch
trend over time; **(B) rates system performance** — each score explains itself by what the EMS did well or
missed; **(C) shows the money** — € saved as a supporting layer. Two parts: an **energy-flow report**
(where energy came from and went, per period) and **three scores**.

**Normalization rule (applies to every score tile): 0–100, higher is better, 100 = best.**

## Non-goals (YAGNI)

- **Not** a rebuild of Home Assistant's Energy Dashboard. HA already shows raw grid/solar/battery flows,
  cost, gas m³, and (with the Electricity Maps integration) a low-carbon %. Our differentiated value is the
  **graded, trending scores**, the **control-linked "system performance"** read (only *we* own the
  decisions), the **combined electricity+gas footprint**, and **car-as-attributed-sink**. We do not
  replicate HA's per-device granularity.
- **Not** an intraday gas optimizer (NL gas is a *daily* price).
- No new control behavior. This feature only reads recorded history.

## Part 1 — The three scores

Each tile: a 0–100 score (100 = best), a supporting raw number, and a one-line **self-explanation** (B).

### ① Self-consumption score
- **Score** = `solar used on-site ÷ solar produced × 100` where used on-site = `solar→home + solar→car +
  solar→battery`. **100 = exported nothing / kept all solar.**
- **Companion number (not the score):** self-sufficiency = `(house+car load − grid import) ÷ load × 100`.
- **Self-explanation examples:** "kept 91% of your solar; exported 3.2 kWh you couldn't store or use."
- **Edge cases:** window with ~no solar → tile falls back to self-sufficiency (or "—"); period reports
  (week/month) aggregate energy first, so the ratio is well-defined.

### ② CO₂ score
- **Raw number (shown, lower = better):** total footprint kg CO₂ = `grid_import_kWh × grid_factor +
  gas_m3 × gas_factor`.
- **Score (100 = best):** **% avoided vs. a reference home** with no solar/battery/EMS:
  `(baseline_kg − your_kg) ÷ baseline_kg × 100`, clamped [0,100]. Baseline imports the *whole* electricity
  load at grid intensity and burns the *same* gas.
  - Electricity term reduces to solar/battery self-supply × grid_factor (you avoid what you didn't import).
  - **Gas behaviour (intended, must be surfaced in UI):** until heating control exists, gas is identical in
    baseline and actual, so it contributes **0 to "avoided" but stays in the denominator** — i.e. once gas
    is folded in, the score **steps down** to reflect that heating is still fossil. This is deliberate: it
    surfaces the biggest remaining lever ("gas is 71% of your footprint"). Tile annotation must explain the
    step-down when gas first appears.
- **Self-explanation examples:** "avoided 62% of a no-solar home's emissions"; after gas: "…but gas heating
  is 71% of your footprint — the biggest remaining cut."
- **Phasing / data:** `grid_factor` starts as a **fixed config value** (~0.27 kg/kWh, editable) — no external
  dependency for v1. `gas_factor` ≈ 1.78 kg/m³. Gas term is **0 until gas ingestion (roadmap F1)** lands,
  then folds in automatically. A time-resolved NED.nl carbon signal is an **optional later enhancement**
  (only matters for rewarding *when* you imported, a minor lever in NL) — not required for this feature.

### ③ Best-price score
- **Score** = `(P_max − your_import_VWAP) ÷ (P_max − P_min) × 100` over the period, where `your_import_VWAP`
  is your grid-import volume-weighted average price and `[P_min, P_max]` is the period's price range.
  **100 = imported all grid energy at the cheapest hours; 0 = at the priciest.**
- **Supporting € (B/C):** "≈ €X saved vs. a no-shifting day" (replay: same loads, no battery arbitrage,
  import-as-needed).
- **Self-explanation examples:** "you drew power in the 8 cheapest hours of the day."
- **Edge case:** imported nothing (fully self-sufficient window) → 100 ("didn't need the grid").

## Part 2 — Energy-flow report

Extends the existing solar-first allocation (`ems/energy_flow.py`) with the **car** as a sink.

**Inputs per slot (from stored history — no new columns needed):** solar (`solar_power_w`), grid
(`grid_power_w`, ± net), battery (`battery_power_w`, + discharge / − charge), **home load** = derived
`non_ev_load_w`, **car load** = derived `house_load_w − non_ev_load_w`. Balance holds by construction:
`solar + battery_discharge + grid_import = home + car + battery_charge + grid_export`.

**Per-slot allocation (15-min, summed over the period) — solar-first, home-before-car:**
- **Solar** → home → car → battery → export.
- **Battery** discharge → home **first**, then → car. The `battery→car` band is the **honest measured
  leak** — non-zero exactly when the battery discharged *more than the home needed* while the car was
  charging (the car-guard failing). When the guard works it is 0 *by the data*, not by assumption — which
  makes it a real diagnostic, surfaced when it exceeds a small threshold (payoff from this session's fix).
- **Grid** → the remainder: home + car not covered by solar/battery, plus grid-charging. A rare
  `battery→grid` term is added only if the battery discharged beyond all local load, so sources = sinks.

**Headline totals (the five requested + secondary):**
| Total | Definition |
|---|---|
| from solar | solar→home + solar→car + solar→battery + solar→grid |
| from battery | battery→home + battery→car + battery→grid |
| from grid | grid→home + grid→car + grid→battery |
| to house | solar→home + battery→home + grid→home |
| to car | solar→car + battery→car + grid→car |
| export (secondary) | solar→grid + battery→grid |
| battery charge (secondary) | solar→battery + grid→battery |
| **car-guard leak (diagnostic)** | battery→car — should be ~0; >0 means the battery fed the car |

**Visual:** reuse the existing Sankey (extended with the car band). The report = totals + Sankey + the three
scores over the same window.

**Periods:** **Day / Week / Month / Year**, aggregated from the 15-min slots already in SQLite. Each score
is computed over the selected window; the headline UI trends the score across windows (A).

## Part 3 — Data, storage, architecture

Data is all present — grid/solar/battery/**ev** in `raw_samples`, `house_load_w`/`non_ev_load_w` in
`derived_samples`, 365-day retention, `raw_between`/`derived_between` query helpers. **No schema change.**

**Module design (interfaces):**
- **`ems/energy_flow.py` (extend):** `_allocate_slot(solar_w, grid_w, battery_w, home_w, car_w) ->
  Bands` returning the ~10 bands (solar_{home,car,batt,grid}, grid_{home,car,batt}, batt_{home,car,grid});
  add `solar_to_car/battery_to_car/grid_to_car`, `car_kwh`, `solar_self_consumption_pct`,
  `car_guard_leak_kwh` to `EnergyFlows`; rename/generalise `build_daily_flows` → `build_flows(raw_rows,
  derived_rows, start, end, *, label, partial)` for any window (a thin `build_daily_flows` wrapper kept).
- **`ems/scores.py` (new, pure):** `self_consumption_score(flows) -> Score`, `co2_score(flows, *,
  grid_factor, gas_factor, gas_m3=0.0) -> Score`, `best_price_score(import_by_slot, prices) -> Score`,
  where `Score = {value: 0..100, raw: float, unit: str, explanation: str}`. No I/O; fully unit-tested.
- **`ems/reporting.py` (new, thin orchestration):** `build_report(store, prices, period, anchor_date,
  cfg) -> Report` — resolves the window, pulls `raw_between`/`derived_between`, calls `build_flows` +
  the three scores, and (later) a short trend series. Keeps I/O out of the pure modules.
- **API:** `GET /api/report?period=day|week|month|year&date=YYYY-MM-DD` → `{window, flows, totals,
  scores[], trend[]}`. Config knobs in the settings store: `reporting.grid_co2_factor` (default 0.27),
  `reporting.gas_co2_factor` (default 1.78).
- **Frontend:** a new **Insights** view (`view: "insights"`) in `App.tsx` with a nav button — three score
  tiles (value + trend spark + self-explanation), the flow totals, and the Sankey (reuse
  `EnergyDistribution`, extended with the car band). Poll only while the Insights tab is active.

## Constraints respected

- **Read-only / no control risk** — never touches the battery writer; pure history + arithmetic.
- **Standalone** — computed from local SQLite; survives an HA outage (product principle); no dependency on
  HA for any of it.
- **Explainability-first** — every score carries a human-readable reason (the B story), consistent with the
  product's core differentiator.
- **Privacy/local-first** — v1 uses local data + config factors only; the optional NED.nl carbon feed (later)
  is a single cache-friendly outbound call.

## Testing

- Unit: `_allocate_slot` with a car load (solar→car, grid→car, battery→car≈0); conservation (sources = sinks
  per slot); window aggregation.
- Unit: each score formula incl. edge cases — no solar (self-consumption falls back), no import (best-price =
  100), gas = 0 vs gas > 0 (CO₂ step-down), clamping.
- Unit: € "vs no-shifting" counterfactual.
- API/e2e: `/api/report` for each period against a seeded history DB (per the clean-DB test rule).

## Phasing

- **v1 (this spec):** electricity-only, fixed carbon factor, all three scores + car-inclusive flow report +
  Day/Week/Month/Year. Requires `ev_power_w` persisted.
- **Later (roadmap F1):** gas ingested from P1 → CO₂ score folds in gas (with the step-down annotation).
- **Optional (roadmap F3):** time-resolved NED.nl carbon intensity refines the CO₂ raw number (minor in NL).

## Resolved during review (2026-07-01)

1. **`ev_power_w` is persisted** in `raw_samples`, and `derived_samples` carries `house_load_w` +
   `non_ev_load_w` — so **car load = `house_load_w − non_ev_load_w`** and historical car flows are fully
   available. No schema change.
2. **Solar→home before solar→car** (home-first) — cosmetic for the Sankey; totals are order-independent.
3. **`battery→car` is a measured leak band**, not an assumed zero — it is the car-guard diagnostic.
4. **Score trend** ships in v1 (a short spark-line of the last N same-length periods) since motivation (A)
   is the headline goal.

## Build order (10 loops)

Dev: (1) extend `energy_flow.py` + car allocation + tests; (2) `scores.py` + tests; (3) `reporting.py` +
`/api/report` + tests; (4) frontend Insights view (tiles + totals + Sankey); (5) integration + e2e + full
suite. Polish: (6) copy/self-explanations; (7) edge-case hardening; (8) visual polish + trend sparks;
(9) accessibility + light/dark + responsive; (10) docs (SPEC.md/README) + final test/ruff/build sweep.
