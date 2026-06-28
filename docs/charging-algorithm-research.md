# Charging algorithm — research, validation & improvement

> Companion to `SPEC.md` §8. How the battery charging logic was validated against realistic NL
> weather, the flaw that was found, the improvement that was made, and the evidence for it. The
> backtest harness lives in `ems/sim.py`; reproduce with the scripts referenced below.

## 1. What we validated

The charger decides **when and how much to charge** (from solar + the grid). Two planners existed:
`summer` (solar-first, grid-tops-up to a fixed night-carry target) and `winter` (price arbitrage,
fixed cheapest-N charge window). We built a backtest (`ems/sim.py`) that runs a planner's plan
through the energy model against a **realised** NL day and scores grid cost, self-sufficiency, the
SoC floor and cycles — for four weather types (bad / average / good / extreme, 3 kWp, ~10 kWh/day
load, Tibber-shaped prices).

Two backtest modes:
- **single-shot** — plan once, score the day.
- **rolling** — re-plan every slot with the current SoC and apply only that slot's action. This is
  what the live loop actually does and is the honest test.

## 2. What we found (the flaw)

Under **rolling** replanning the current charger performed far worse than its single-shot numbers
suggested — total 4-day grid cost **€8.20**, self-sufficiency as low as 0–4% on dull days.

Root cause (traced): the summer **night-carry target is a fixed constant** (`overnight_load_kwh`)
that (a) **under-sizes** the real evening+overnight load on dull days, so the battery drains *during
the expensive evening peak* and imports at peak price; and (b) the planner **grid-charges to the
target overnight (01:00–04:00) even when the next morning's sun will refill it for free** — ~8 kWh
of pointless overnight import on a sunny day. The single-shot test missed (b) because it never
re-plans at 01:00.

## 3. The improvement — adaptive demand-aware peak-shaving (`ems/planner/adaptive.py`)

One season-agnostic charger:
- Sizes the battery to the **forecast** evening+overnight deficit (`load − P50 solar`, capped at
  usable − reserve) — demand-aware, not a fixed constant.
- Nets out the **P10** (conservative) solar that will charge it, and grid-charges only the
  **shortfall**, in the cheapest slots **before** the expensive window, so the pack is full going
  into the peak and **shaves** it. Because it nets upcoming solar, it does **not** grid-charge
  overnight when tomorrow is sunny.

## 4. The evidence (backtest, rolling, 4 NL days)

| Day | Current cost | Adaptive cost | Optimal (DP) | Adaptive self-suf |
|---|---|---|---|---|
| bad | €3.65 | €2.07 | €2.06 | 29% |
| average | €2.61 | €1.00 | €0.96 | 64% |
| good | €1.29 | −€0.05 | −€0.07 | 85% |
| extreme | €0.65 | −€0.69 | −€0.72 | 100% |
| **total** | **€8.20** | **€2.32** | **€2.23** | |

- Adaptive cuts 4-day grid cost **€8.20 → €2.32 (−72%)** and never discharges below reserve.
- A **dynamic-programming optimizer** (`ems/planner/optimal.py`) computes the globally cheapest
  schedule as a yardstick: adaptive is within **4% (€0.09)** of optimal — and keeps **higher
  self-sufficiency** than the pure cost-optimizer (which trades autonomy for marginal arbitrage).
  So we ship the simpler, interpretable, near-optimal heuristic; the DP stays as a yardstick.
- **Robust to forecast error:** with the forecast 40% too rosy, P10 sizing + replanning keep the
  battery above reserve on every day (the safety guarantee holds).

## 5. Why DP, not a trained ML model (for now)

The objective (minimise grid cost, never cross reserve) is exact and the state is 1-D (SoC), so DP
gives the optimum with no training data — which we don't have yet (forecast/outcome pairs aren't
logged). The natural ML next step, once logged: a **solar-forecast bias correction** (learn this
roof's forecast-vs-actual ratio) to sharpen the P10/P50 the charger sizes against. Behind
`ports.py`, accelerator-gated, never bypassing the §8.11 validator.

## 6. Status

`plan_adaptive` is the live **summer** engine (`strategy.build_plan`); winter still uses the
arbitrage planner. All planners emit the same `Plan` and pass the unchanged projection/validator.
Tests: unit (`test_adaptive.py`, `test_optimal.py`) + backtest regressions (`test_sim.py`:
adaptive ≤ current, adaptive within €0.50 of optimal, safe under a rosy forecast).
