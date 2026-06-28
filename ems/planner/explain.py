"""Build the "what will the algorithm do next 24h" detail (SPEC §9.1).

Joins the plan, prices and solar forecast onto ONE shared timeline (the plan's own 15-min slots,
starting at the current slot) so the dashboard can render them aligned — the cheap price windows
line up exactly with the charge actions. Pure + unit-tested.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner.projection import SLOT_HOURS, ProjectedSlot
from ems.planner.schedule import Plan
from ems.savings import estimate_daily_savings_eur
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


def plan_metrics(plan: Plan, prices: list[PriceSlot]) -> dict:
    """Headline metrics for a plan — used to show the IMPACT of a settings change (before/after)."""
    price_by = {p.start: p.eur_per_kwh for p in prices}
    counts = Counter(s.intent for s in plan.slots)
    return {
        "summary": _summary(plan.slots, price_by),
        "savings_eur": round(estimate_daily_savings_eur(plan, price_by), 2),
        "charge_slots": counts.get(BatteryIntent.GRID_CHARGE_TO_TARGET, 0),
        "discharge_slots": counts.get(BatteryIntent.DISCHARGE_FOR_LOAD, 0),
        "hold_slots": counts.get(BatteryIntent.HOLD_RESERVE, 0),
        "self_consume_slots": counts.get(BatteryIntent.ALLOW_SELF_CONSUMPTION, 0),
    }


def summarize_projection(projected: list[ProjectedSlot]) -> dict:
    """Headline numbers + a plain-English narrative of the projected next-24h energy behaviour.
    Clock times are left to the UI (the timestamps are returned); the text stays tz-agnostic.
    `*_kwh` integrate power over the 15-min slots (energy = W × 0.25 h ÷ 1000)."""
    if not projected:
        return {"summary": "No projection yet.", "soc_end_pct": None, "soc_min_pct": None,
                "soc_max_pct": None, "soc_min_at": None, "soc_max_at": None,
                "import_kwh": 0.0, "export_kwh": 0.0, "solar_kwh": 0.0, "load_kwh": 0.0}
    lo = min(projected, key=lambda p: p.soc_pct)
    hi = max(projected, key=lambda p: p.soc_pct)
    end = projected[-1].soc_pct
    imp = sum(p.grid_w for p in projected if p.grid_w > 0) * SLOT_HOURS / 1000.0
    exp = sum(-p.grid_w for p in projected if p.grid_w < 0) * SLOT_HOURS / 1000.0
    solar = sum(p.solar_w for p in projected) * SLOT_HOURS / 1000.0
    load = sum(p.load_w for p in projected) * SLOT_HOURS / 1000.0
    # Honest, shape-agnostic phrasing: report peak / end / lowest as facts (the "lowest" is often
    # just the starting slot, so never imply a mid-window "dip" that doesn't happen). "Planned
    # window" not "24h" — the horizon is only as long as prices are published (≈11h until tomorrow).
    summary = (
        f"Projected SoC peaks at {round(hi.soc_pct)}% and ends the planned window near "
        f"{round(end)}% (lowest {round(lo.soc_pct)}%). Projected grid: {imp:.1f} kWh in / "
        f"{exp:.1f} kWh out, on {solar:.1f} kWh solar and {load:.1f} kWh of load."
    )
    return {
        "summary": summary,
        "soc_end_pct": round(end, 1),
        "soc_min_pct": round(lo.soc_pct, 1), "soc_min_at": lo.start.isoformat(),
        "soc_max_pct": round(hi.soc_pct, 1), "soc_max_at": hi.start.isoformat(),
        "import_kwh": round(imp, 2), "export_kwh": round(exp, 2),
        "solar_kwh": round(solar, 2), "load_kwh": round(load, 2),
    }


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
