"""Daily finance: what the grid cost, what the battery cost in wear, and what the EMS saved —
measured from recorded history, not from the plan (backlog B-03; spec 2026-07-03).

Pure — the caller supplies one local day's raw rows and stored price slots. The baseline is the
"no battery, same solar + loads" counterfactual: removing the battery from the meter balance gives
`grid'_w = grid_w + battery_w` per slot (battery + grid + solar = load). Export is credited via
`economics.export_value` under the configured feed-in model — default `net_metering` (full price,
today's saldering); `spot_minus_tax` / `fixed` model the post-2027 world (B-05) — applied to BOTH
the actual and the baseline cost so the comparison stays honest (under a low feed-in the baseline
household, which exports more, is penalised more, so the battery's measured benefit honestly grows).
Fixed fees and taxes are the same in both worlds and cancel out of `saved_eur`.

Battery wear is charged per **kWh discharged** (`degradation_eur_per_kwh`), which prices a
charge→discharge cycle once on the energy delivered — the same basis the planner spends in its
arbitrage break-even.

Every € figure is computed over the SAME priced slots (cost, baseline, and the wear inside
`saved`), so a partial-price day yields a correct partial-window saving — it can't mix partial
revenue with a full day of wear. A day with no priced slots at all reports energy only (€ = None);
otherwise `price_coverage` (0..1) signals how much of the day the money figures cover.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from ems.planner.economics import export_value
from ems.retrospect import _floor, _mean, _parse

_DH = 15 / 60.0  # hours per 15-min slot


@dataclass(frozen=True)
class DayFinance:
    day: str  # local YYYY-MM-DD
    has_data: bool
    price_coverage: float  # 0..1 — share of sampled slots with a stored price
    grid_cost_eur: float | None
    battery_cost_eur: float | None
    baseline_cost_eur: float | None
    saved_eur: float | None
    grid_import_kwh: float
    grid_export_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float

    def to_dict(self) -> dict:
        def r2(x: float | None) -> float | None:
            return None if x is None else round(x, 2)

        return {
            "day": self.day, "has_data": self.has_data,
            "price_coverage": round(self.price_coverage, 3),
            "grid_cost_eur": r2(self.grid_cost_eur),
            "battery_cost_eur": r2(self.battery_cost_eur),
            "baseline_cost_eur": r2(self.baseline_cost_eur),
            "saved_eur": r2(self.saved_eur),
            "grid_import_kwh": round(self.grid_import_kwh, 2),
            "grid_export_kwh": round(self.grid_export_kwh, 2),
            "battery_charge_kwh": round(self.battery_charge_kwh, 2),
            "battery_discharge_kwh": round(self.battery_discharge_kwh, 2),
        }


def day_finance(
    raw_rows: list[dict],
    price_rows: list[dict],
    *,
    day: str,
    degradation_eur_per_kwh: float = 0.05,
    export_price_model: str = "net_metering",
    energy_tax_eur_per_kwh: float = 0.13,
    fixed_feed_in_eur_per_kwh: float = 0.01,
) -> DayFinance:
    """One day's finance from raw samples (`ts`, `grid_power_w`, `battery_power_w`; the caller
    windows the rows to the local day) and stored price slots (`start_ts`, `eur_per_kwh`).

    `export_price_model` (+ `energy_tax_eur_per_kwh` / `fixed_feed_in_eur_per_kwh`) picks how
    exported energy is valued (see module docstring / `economics.export_value`); the default
    `net_metering` credits export at the full spot price — today's saldering behaviour."""
    grid_by: dict[datetime, list[float]] = defaultdict(list)
    batt_by: dict[datetime, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None:
            continue
        slot = _floor(dt)
        grid_by[slot].append(float(r.get("grid_power_w", 0.0)))
        batt_by[slot].append(float(r.get("battery_power_w", 0.0)))

    price_by: dict[datetime, float] = {}
    for p in price_rows:
        dt = _parse(p.get("start_ts"))
        if dt is not None:
            price_by[_floor(dt)] = float(p.get("eur_per_kwh", 0.0))

    imp = exp = chg = dis = 0.0  # full-day physical energy (always reported)
    dis_priced = 0.0             # discharge over PRICED slots only → wear inside `saved`
    cost = base_cost = 0.0
    priced = 0
    for slot in sorted(grid_by):
        grid_w = _mean(grid_by[slot])
        batt_w = _mean(batt_by[slot])  # + discharge / − charge
        imp += max(0.0, grid_w) * _DH / 1000.0
        exp += max(0.0, -grid_w) * _DH / 1000.0
        dis += max(0.0, batt_w) * _DH / 1000.0
        chg += max(0.0, -batt_w) * _DH / 1000.0
        price = price_by.get(slot)
        if price is None:
            continue
        priced += 1
        # Import costs the full price; export earns the feed-in VALUE (full price under saldering,
        # less post-2027 — may even be negative). Same credit in both worlds so `saved` stays fair.
        credit = export_value(price, model=export_price_model,
                              energy_tax_eur_per_kwh=energy_tax_eur_per_kwh,
                              fixed_feed_in_eur_per_kwh=fixed_feed_in_eur_per_kwh)
        cost += (max(0.0, grid_w) * price - max(0.0, -grid_w) * credit) * _DH / 1000.0
        baseline_w = grid_w + batt_w  # the meter with the battery removed
        base_cost += (max(0.0, baseline_w) * price
                      - max(0.0, -baseline_w) * credit) * _DH / 1000.0
        dis_priced += max(0.0, batt_w) * _DH / 1000.0

    n_slots = len(grid_by)
    coverage = priced / n_slots if n_slots else 0.0
    # Give € figures whenever ANY slot is priced. Charging wear only over PRICED-slot discharge
    # (`dis_priced`) keeps cost, baseline and wear on the SAME window, so a partial-price day yields
    # a correct partial-window saving (never full-day wear against priced-only revenue). Confidence
    # is signalled by `price_coverage`, not by blanking the numbers.
    if priced:
        battery_cost = dis_priced * degradation_eur_per_kwh
        saved = base_cost - cost - battery_cost
        return DayFinance(day, True, coverage, cost, battery_cost, base_cost, saved,
                          imp, exp, chg, dis)
    # No priced slots at all → can't compute money figures; report energy only.
    return DayFinance(day, n_slots > 0, coverage, None, None, None, None, imp, exp, chg, dis)
