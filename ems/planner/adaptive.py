"""Adaptive demand-aware charging (SPEC §8.3 "size to serve the load, not a fixed ceiling").

One algorithm for both seasons. It sizes the battery to the energy it should actually deliver over
the coming evening + night — computed from the FORECAST load minus the forecast solar — rather than
a fixed night-carry constant (which the backtest showed under-sizes on dull days and leaves the
battery draining mid-peak at peak price). It then grid-charges only the shortfall solar won't cover,
in the cheapest slots *before* the expensive window, so the battery is full going into the peak and
shaves it. Risk-aware: the solar it counts on is the conservative P10.

Pure + unit-tested. The caller supplies the expected load per slot (the learned profile).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

_DH = 0.25  # hours / slot


@dataclass(frozen=True)
class AdaptiveConfig:
    usable_kwh: float
    reserve_soc_pct: float = 10.0
    round_trip_efficiency: float = 0.90
    max_charge_w: float = 4000.0
    degradation_eur_per_kwh: float = 0.05
    risk_margin_eur_per_kwh: float = 0.02
    horizon_slots: int = 96


def plan_adaptive(
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    now: datetime,
    *,
    soc_pct: float,
    load_w_by: dict[datetime, float],
    cfg: AdaptiveConfig,
) -> Plan:
    future = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    if not future:
        return Plan(created_at=now, slots=())

    p50 = {f.start: f.p50_w for f in forecast}
    p10 = {f.start: f.p10_w for f in forecast}
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    usable = cfg.usable_kwh
    reserve_kwh = cfg.reserve_soc_pct / 100.0 * usable
    avail_now_kwh = max(0.0, soc_pct / 100.0 * usable - reserve_kwh)

    # The price at which charging starts to pay (cheapest-quartile price + round-trip losses).
    ranked = sorted(p.eur_per_kwh for p in future)
    charge_price = ranked[len(ranked) // 4]
    breakeven = (charge_price / cfg.round_trip_efficiency
                 + cfg.degradation_eur_per_kwh + cfg.risk_margin_eur_per_kwh)

    def net_w(p: PriceSlot) -> float:  # + deficit (load over solar) / − surplus
        return load_w_by.get(p.start, 0.0) - p50.get(p.start, 0.0)

    # Energy the battery should deliver over the horizon = the forecast deficit, capped at what the
    # pack can actually hold above its reserve. (Demand-aware target, not a fixed constant.)
    # `total_deficit_kwh` is AC load to serve; the pack stores DC. To deliver D kWh at the AC
    # terminals the pack must hold D/eta kWh DC, so we convert the AC deficit to its DC requirement
    # before capping at the (DC) headroom — `coverable_kwh` is then a DC quantity, consistent with
    # `avail_now_kwh`, `solar_to_batt_kwh` and `per_slot_kwh` below.
    total_deficit_kwh = sum(max(0.0, net_w(p)) * _DH / 1000.0 for p in future)
    coverable_kwh = min(total_deficit_kwh / eta, usable - reserve_kwh)
    # Conservative (P10) solar that will flow into the battery from daytime surplus.
    solar_to_batt_kwh = sum(
        max(0.0, p10.get(p.start, 0.0) - load_w_by.get(p.start, 0.0)) * _DH / 1000.0 * eta
        for p in future
    )
    shortfall_kwh = max(0.0, coverable_kwh - avail_now_kwh - solar_to_batt_kwh)

    # Serve the EXPENSIVE deficit slots from the battery (peak-shave); buy the shortfall BEFORE the
    # last of them so the pack is full going in.
    expensive = [p.start for p in future if net_w(p) > 0 and p.eur_per_kwh > breakeven]
    discharge_set = set(expensive)
    last_need = max(expensive) if expensive else None
    per_slot_kwh = cfg.max_charge_w * _DH / 1000.0 * eta
    n_charge = (math.ceil(shortfall_kwh / per_slot_kwh)
                if per_slot_kwh > 0 and shortfall_kwh > 1e-9 else 0)
    if discharge_set:
        # Shaving a peak: any pre-peak slot CHEAPER THAN THE PEAK is worth charging in (it beats
        # importing at the peak). Using this — not the strict break-even — avoids silently
        # under-charging when too few slots sit below break-even, so the pack still fills.
        peak_min = min(p.eur_per_kwh for p in future if p.start in discharge_set)
        pool = [p for p in future if p.start < last_need and p.eur_per_kwh < peak_min]
    else:
        # No expensive peak ahead: only charge if genuinely cheap, to avoid pointless cycling.
        pool = [p for p in future if p.eur_per_kwh <= breakeven]
    pool.sort(key=lambda p: (p.eur_per_kwh, p.start))
    charge_set = {p.start for p in pool[:n_charge]}

    out: list[PlanSlot] = []
    for p in future:
        solar = p50.get(p.start, 0.0)
        if p.start in charge_set:
            intent = BatteryIntent.GRID_CHARGE_TO_TARGET
            reason = f"charge: cheap €{p.eur_per_kwh:.2f}/kWh to cover the coming peak + night"
        elif p.start in discharge_set:
            intent = BatteryIntent.DISCHARGE_FOR_LOAD
            reason = f"shave the €{p.eur_per_kwh:.2f}/kWh peak from the battery"
        elif solar > load_w_by.get(p.start, 0.0):
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = f"solar-first: charging from your panels ({solar:.0f} W)"
        else:
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = "running the house on the battery"
        out.append(PlanSlot(p.start, intent, reason))
    return Plan(created_at=now, slots=tuple(out))
