"""Build the "what will the algorithm do next 24h" detail (SPEC §9.1).

Joins the plan, prices and solar forecast onto ONE shared timeline (the plan's own 15-min slots,
starting at the current slot) so the dashboard can render them aligned — the cheap price windows
line up exactly with the charge actions. Pure + unit-tested.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner.schedule import Plan
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

_INTENT_LABEL = {
    BatteryIntent.ALLOW_SELF_CONSUMPTION: "self-consume",
    BatteryIntent.GRID_CHARGE_TO_TARGET: "charge",
    BatteryIntent.HOLD_RESERVE: "hold",
    BatteryIntent.DISCHARGE_FOR_LOAD: "discharge",
}


def _summary(slots, price_by: dict[datetime, float]) -> str:
    """A one-line plain-English summary of the next-24h plan."""
    if not slots:
        return "No plan yet."
    counts = Counter(s.intent for s in slots)
    charge = [price_by[s.start] for s in slots
              if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET and s.start in price_by]
    discharge = [price_by[s.start] for s in slots
                 if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD and s.start in price_by]
    parts: list[str] = []
    if charge:
        parts.append(f"charge {len(charge)}×15m at ≤€{max(charge):.2f}/kWh")
    if discharge:
        parts.append(f"discharge {len(discharge)}×15m at ≥€{min(discharge):.2f}/kWh")
    if counts.get(BatteryIntent.HOLD_RESERVE):
        parts.append(f"hold {counts[BatteryIntent.HOLD_RESERVE]}×15m")
    sc = counts.get(BatteryIntent.ALLOW_SELF_CONSUMPTION, 0)
    if sc:
        parts.append(f"self-consume {sc}×15m")
    return "Next 24h — " + ", ".join(parts) + "." if parts else "Next 24h — self-consumption."


def build_plan_detail(
    now: datetime,
    prices: list[PriceSlot],
    plan: Plan,
    forecast_slots: list[ForecastSlot] | None,
    horizon: int = 96,
) -> dict:
    """Per-slot {start, intent, reason, eur_per_kwh, solar_w} on the plan's timeline + a summary."""
    price_by = {p.start: p.eur_per_kwh for p in prices}
    fc_by = {f.start: f.p50_w for f in (forecast_slots or [])}
    window = plan.slots[:horizon]
    cur = plan.intent_at(now)
    return {
        "current_intent": cur.intent if cur else None,
        "summary": _summary(window, price_by),
        "slots": [
            {
                "start": s.start.isoformat(),
                "intent": s.intent,
                "label": _INTENT_LABEL.get(s.intent, str(s.intent)),
                "reason": s.reason,
                "eur_per_kwh": price_by.get(s.start),
                "solar_w": fc_by.get(s.start),
            }
            for s in window
        ],
    }
