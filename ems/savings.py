"""Estimated arbitrage savings for a plan (SPEC §9.1 savings panel). A rough, illustrative
*net* figure: per discharge slot, the margin over the cost to have stored that energy —
charge price / efficiency PLUS degradation + risk. The delivered-energy cost is priced at the
*worst* (highest) of the plan's charge slots — a deliberately conservative basis so the figure is
a lower bound and never overclaims (GOAL §2/§5). Note this is more conservative than the planner's
own break-even, which sizes against the cheapest-quartile charge price.
Real savings will use measured energy later."""
from __future__ import annotations

from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner.schedule import Plan

_SLOT_HOURS = 0.25


def estimate_daily_savings_eur(
    plan: Plan,
    price_by_start: dict[datetime, float],
    *,
    efficiency: float = 0.90,
    discharge_kw: float = 1.5,
    degradation_eur_per_kwh: float = 0.05,
    risk_margin_eur_per_kwh: float = 0.02,
) -> float:
    """Net €: sum over discharge slots of (discharge_price − delivered_cost) × per-slot energy,
    where delivered_cost = max_charge_price/efficiency + degradation + risk. 0.0 on no-trade."""
    charge_prices = [
        price_by_start[s.start]
        for s in plan.slots
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET and s.start in price_by_start
    ]
    if not charge_prices:
        return 0.0
    delivered_cost = (
        max(charge_prices) / efficiency + degradation_eur_per_kwh + risk_margin_eur_per_kwh
    )
    energy = discharge_kw * _SLOT_HOURS
    total = 0.0
    for s in plan.slots:
        if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD and s.start in price_by_start:
            margin = price_by_start[s.start] - delivered_cost
            if margin > 0:
                total += margin * energy
    return round(total, 2)
