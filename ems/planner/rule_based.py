"""Rule-based winter-arbitrage planner (SPEC §8.3, simplified first cut).

Charge the cheapest window, discharge the expensive peaks — but ONLY when the spread beats
round-trip losses + degradation + a risk margin (the profitability test). On a flat/low-spread
day it returns no-trade (all ALLOW_SELF_CONSUMPTION). M-later will add target-SoC, deadlines,
the projected-SoC curve, and the ML planner behind the same Plan interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.prices import PriceSlot


@dataclass(frozen=True)
class PlannerConfig:
    round_trip_efficiency: float = 0.90
    degradation_eur_per_kwh: float = 0.05
    risk_margin_eur_per_kwh: float = 0.02
    charge_slots: int = 12  # ~3h of the cheapest slots
    discharge_slots: int = 24  # up to ~6h of the most expensive slots
    horizon_slots: int = 96  # next ~24h


def _all_auto(prices: list[PriceSlot], now: datetime, note: str) -> Plan:
    slots = tuple(
        PlanSlot(
            p.start,
            BatteryIntent.ALLOW_SELF_CONSUMPTION,
            f"{note} (€{p.eur_per_kwh:.2f}/kWh)",
        )
        for p in prices
    )
    return Plan(created_at=now, slots=slots, strategy="winter")


def plan_rule_based(
    prices: list[PriceSlot], now: datetime, cfg: PlannerConfig | None = None
) -> Plan:
    cfg = cfg or PlannerConfig()
    horizon = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    if not horizon:
        return Plan(created_at=now, slots=())

    by_price = sorted(horizon, key=lambda p: p.eur_per_kwh)
    charge_candidates = by_price[: cfg.charge_slots]
    charge_price = max((p.eur_per_kwh for p in charge_candidates), default=0.0)
    # A discharge slot only pays if it beats the cost of the energy we'd store to serve it.
    breakeven = (
        charge_price / cfg.round_trip_efficiency
        + cfg.degradation_eur_per_kwh
        + cfg.risk_margin_eur_per_kwh
    )

    by_price_desc = sorted(horizon, key=lambda p: -p.eur_per_kwh)
    discharge_set = {
        p.start for p in by_price_desc[: cfg.discharge_slots] if p.eur_per_kwh > breakeven
    }
    if not discharge_set:
        # No profitable peak -> no-trade: never cycle the battery for nothing (SPEC §8.3).
        return _all_auto(horizon, now, "no-trade: spread below break-even")

    charge_set = {p.start for p in charge_candidates}
    out: list[PlanSlot] = []
    for p in horizon:
        has_later_discharge = any(d > p.start for d in discharge_set)
        if p.start in charge_set and has_later_discharge:
            intent = BatteryIntent.GRID_CHARGE_TO_TARGET
            reason = f"charge: cheap window €{p.eur_per_kwh:.2f}/kWh"
        elif p.start in charge_set:
            # Cheap, but no profitable peak remains to discharge into -> don't cycle for nothing.
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = f"self-consumption: cheap but no peak ahead (€{p.eur_per_kwh:.2f}/kWh)"
        elif p.start in discharge_set:
            intent = BatteryIntent.DISCHARGE_FOR_LOAD
            reason = f"discharge: €{p.eur_per_kwh:.2f}/kWh > break-even €{breakeven:.2f}"
        elif has_later_discharge and any(c < p.start for c in charge_set):
            intent = BatteryIntent.HOLD_RESERVE
            reason = f"hold cheap energy for the coming peak (now €{p.eur_per_kwh:.2f}/kWh)"
        else:
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = f"self-consumption (€{p.eur_per_kwh:.2f}/kWh)"
        out.append(PlanSlot(p.start, intent, reason))
    # Deadline = the first profitable peak: the cheap window must have charged before it.
    # (Slot-level target SoC for winter arbitrage is sized in the demand-aware upgrade, Polish 2.)
    first_peak = min(discharge_set) if discharge_set else None
    return Plan(created_at=now, slots=tuple(out), strategy="winter", deadline=first_peak)
