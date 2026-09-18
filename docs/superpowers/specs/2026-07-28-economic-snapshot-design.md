# Central economic snapshot

**Date:** 2026-07-28  
**Status:** Approved design  
**Scope:** Economics and explainability architecture batch

## Context

Import costs, export credits, efficiency losses, degradation, risk margins, and grid fees are
currently calculated in several modules. The formulas are similar but accept different settings
and are therefore vulnerable to drift between planner decisions, measured finance, savings, replay,
and EV advice.

## Goals

- Define one immutable, auditable economic input snapshot.
- Centralize pure calculations for delivered energy cost, export credit, net benefit, and break-even.
- Migrate planner economics, finance, and savings first with formula parity tests.
- Surface the assumptions used by report and savings explanations.

## Non-goals

- No change to tariff policy defaults or planner strategy thresholds.
- No immediate rewrite of replay or EV advice; compatibility wrappers remain in this slice.
- No control, battery, storage, or deployment changes.

## Design

Add `ems/economics.py`:

- Frozen `EconomicSnapshot` containing normalized import price/fee, export value/fee inputs,
  round-trip efficiency, degradation allowance, risk margin, and tariff metadata.
- Pure methods: `delivered_energy_cost()`, `export_credit()`, `net_benefit()`, and
  `break_even_import_price()`.
- Factory from the existing `TariffPolicy` plus planner economics settings.
- Serializable metadata suitable for report/savings explanations.

Migrate `ems/planner/economics.py`, `ems/finance.py`, and `ems/savings.py` to use the snapshot or
thin compatibility wrappers around it. Existing output rounding and field names remain unchanged.
Replay and EV advice continue to call compatibility functions and are explicitly tracked for the
next economics slice.

## Testing

- Characterize existing formulas with representative positive, zero, negative, net-metering,
  post-2027 export, fixed-feed-in, fee, efficiency, degradation, and risk cases.
- Assert snapshot methods match the old formulas exactly within existing rounding tolerances.
- Test metadata serialization and report/savings inclusion.
- Run full backend, lint, frontend build, and diff checks.

