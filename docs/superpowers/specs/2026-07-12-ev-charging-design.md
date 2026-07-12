# EV charging support — design (2026-07-12)

Goal: the app tells the user **when to plug in the car** so that a weekly schedule of minimum
charge levels is met as cheaply as possible. Advisory/visual only in v1 (the current charger has
no API); the design leaves a clean seam for a controllable charger later. Web UI + iOS + export.

## Semantics (user-confirmed defaults)

- **Weekly schedule**: per day-of-week → `enabled`, `min_pct`, `ready_by` (default 07:30). The
  minimum must be reached **by the ready-by time** that day ("ready before I leave").
- **Car SoC**: the app cannot read the car. The user sets a manual **anchor** (%, timestamp) in
  Web/iOS; while the car charges, the HomeWizard car meter measures kWh added and the estimate
  rises: `soc = anchor + measured_ac_kwh × η_c / capacity`. **Driving consumption is not
  modeled** (v1 limitation, stated in the UI): SoC only rises with measured charging — the user
  re-anchors after driving. Estimates older than 72 h are flagged stale.
- **Car database**: static curated dataset (`ems/cars.py`) of popular EU EVs → brand/model picker
  in Settings autofills `battery_net_kwh` and `max_ac_kw`; both user-overridable ("custom" car).
- **Effective charge power** = `min(ev.charger_kw, car.max_ac_kw)`.
- **Home battery interplay unchanged**: the existing car-guard (battery HOLD while the car
  charges) is untouched; a regression test pins it.

## The math core (`ems/ev_planner.py`, pure)

### Inputs
`now`, tz; SoC estimate `s0` (pct) + staleness; capacity `C` kWh (net), efficiency `η_c` (AC→
battery, default 0.90), power `P` kW; materialized deadlines `D_1 < … < D_k` with minima `m_i`;
price slots (known horizon only); solar p50 per slot; export-model params (F2).

### Requirements
Battery-side energy need at deadline i: `E_i = max(0, (m_i − s0)/100 × C)`. Because SoC is
non-decreasing in v1 (no driving model), the binding cumulative requirement is
`R_i = max(E_1 … E_i)` (non-decreasing). Constraint: energy charged in slots `≤ D_i` must be
`≥ R_i` for every i.

### Slot economics (consistent with F2)
Effective €/kWh of a slot = full price normally; when forecast solar surplus (`p50 ≥ threshold`),
the slot costs only the **lost feed-in** `export_value(price)` — under `net_metering` these are
equal (pre-2027 behaviour falls out naturally). A slot delivers `c = P × 0.25 × η_c` battery-kWh
and costs `price_eff × P × 0.25` (paid on the AC side).

### Allocation (greedy, provably minimal)
Process deadlines earliest-first; for each, take the **cheapest unallocated slots before that
deadline** until its remaining requirement is covered (final slot may be fractional). With nested
non-decreasing requirements and interval slot-feasibility this greedy is cost-optimal (exchange
argument); correctness is additionally **empirically pinned by brute-force cross-check tests**
(exhaustive subset search on small random instances must match the greedy cost exactly).

The objective is strictly minima-at-least-cost: the greedy never allocates beyond the binding
requirement, even into a slot with negative effective price (free money) — over-charging the car
is not automated. When such a slot is left unallocated, the output's `negative_price_hint` names
the time range so the user can choose to plug in manually; the home battery's
`negative_price_soak` remains the automated response to negative pricing.

### Honesty at the price horizon
Slots are only allocated where prices are known. A deadline beyond the horizon reports its
remainder as `pending_kwh` ("planned once tomorrow's prices arrive") — never silently assumed.
Independent **physical feasibility**: if `remaining_kwh / (P·η_c)` exceeds the hours left before
the deadline (using *all* slots, priced or not), the deadline is flagged infeasible with the
shortfall. No anchor set / empty schedule → no plan, with a prompt to set the SoC.

### Output
`{soc, deadlines: [{ready_by, min_pct, required/planned/pending/shortfall kWh, already_met,
feasible}], slots: [{start, kw, ac_kwh, battery_kwh, eur_per_kwh_effective, est_cost_eur,
solar_surplus, for_deadline}], windows: [contiguous slot groupings with cost + solar share],
advice: one human sentence}` — e.g. *"Plug in tonight 23:00–02:30 (9.5 kWh, ≈ €1.90) to reach
80% by Mon 07:30."*

DST: deadlines materialize in local tz (zoneinfo); tests include the Oct 2026 fall-back day.

## Surfaces

- **API**: `GET /api/car/plan` (plan+advice), `POST /api/car/soc` (anchor; auth-gated,
  audit-logged like overrides), `GET /api/cars` (picker data). Existing
  `/api/advisor/ev-charge` stays for compatibility; the card upgrades to the plan.
- **Web**: Settings "Car" group (picker + schedule editor); dashboard Car card (SoC display +
  quick set, next deadline, plug-in windows over the coming days on a price-coloured strip).
- **iOS**: `CarPanel` between Battery and Strategy (recon-confirmed slot); drill-down detail via
  NavigationLink (BatteryPlanDetailView precedent); SoC quick-set = the app's **first write**
  (mirrors the override POST pattern); demo fixture for screenshots.
- **Export**: `ev_sessions.csv` (detected sessions), car plan snapshot + schedule (no location/
  tokens — redaction audit in polish), validation-summary EV section (planned vs actual
  charging adherence, for algorithm tuning).

## v2 seam (charger API later)
The planner's `slots` output IS the future control schedule; a charger driver would consume it
behind the same intent→confirm pattern as the battery (single writer, dry-run first, per
`docs/v2-ev-control.md` — control stays out of scope until that spec is written).
