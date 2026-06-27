"""Estimated arbitrage savings for a plan (SPEC §9.1 savings panel). A rough, illustrative
figure: for each DISCHARGE slot, the margin over the (efficiency-adjusted) average charge price,
times a nominal per-slot energy. Honest first cut — real savings will use measured energy."""
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
) -> float:
    """Sum of (discharge_price − avg_charge_price/efficiency) × per-slot energy over discharge
    slots; 0.0 when the plan does no charging (no-trade)."""
    charge_prices = [
        price_by_start[s.start]
        for s in plan.slots
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET and s.start in price_by_start
    ]
    if not charge_prices:
        return 0.0
    avg_charge = sum(charge_prices) / len(charge_prices)
    energy = discharge_kw * _SLOT_HOURS
    total = 0.0
    for s in plan.slots:
        if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD and s.start in price_by_start:
            margin = price_by_start[s.start] - avg_charge / efficiency
            if margin > 0:
                total += margin * energy
    return round(total, 2)
