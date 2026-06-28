"""Forward energy projection (SPEC §8.5): the projected-SoC curve + grid flow.

Given the current SoC, the solar forecast, the expected house load and the plan's per-slot intent,
simulate the battery forward slot by slot. This is what lets the dashboard answer "what is my
energy going to do over the next 24 hours" — and lets the user validate the plan before trusting it.

Sign conventions (SPEC §4.1): battery_w is +discharge / −charge; grid_w is +import / −export. The
AC-side balance holds exactly every slot: grid = load − solar − battery.

Round-trip efficiency is split evenly across charge and discharge (η = √rte), so storing then
returning a kWh loses the full round-trip. Charge/discharge are bounded both by the battery's power
limits and by the SoC headroom/availability in the slot, so SoC stays within [0, usable] and never
discharges below the reserve floor. Pure + unit-tested — no I/O.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ems.domain import BatteryIntent
from ems.planner.schedule import SLOT

SLOT_HOURS = SLOT.total_seconds() / 3600.0  # 0.25 h — energy = power × this per slot


@dataclass(frozen=True)
class BatteryModel:
    usable_kwh: float
    max_charge_w: float
    max_discharge_w: float
    round_trip_efficiency: float  # charge→discharge round trip (0–1]
    reserve_soc_pct: float  # never discharge below this SoC


@dataclass(frozen=True)
class ProjectedSlot:
    start: datetime
    intent: BatteryIntent
    soc_pct: float  # SoC at the END of the slot
    battery_w: float  # + discharge / − charge (mean over the slot)
    grid_w: float  # + import / − export
    solar_w: float
    load_w: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def project_energy(
    plan_slots: Iterable,
    *,
    start_soc_pct: float,
    solar_w_by: dict[datetime, float],
    load_w_by: dict[datetime, float],
    model: BatteryModel,
    slot: timedelta = SLOT,
) -> list[ProjectedSlot]:
    """Simulate SoC + grid flow over `plan_slots` (each needs `.start` and `.intent`).

    Contract: `load_w_by` MUST cover every slot start — a missing load silently reads 0 W and
    under-predicts the drain (the API builds it for every plan slot). `solar_w_by` may be sparse;
    a missing entry means 0 W solar (correct for night/pre-dawn slots)."""
    usable = model.usable_kwh
    reserve_kwh = model.reserve_soc_pct / 100.0 * usable
    eta = math.sqrt(_clamp(model.round_trip_efficiency, 1e-6, 1.0))
    dh = slot.total_seconds() / 3600.0
    soc_kwh = _clamp(start_soc_pct, 0.0, 100.0) / 100.0 * usable

    out: list[ProjectedSlot] = []
    for ps in plan_slots:
        solar = solar_w_by.get(ps.start, 0.0)
        load = load_w_by.get(ps.start, 0.0)
        net = load - solar  # + deficit (need power) / − surplus (excess solar)

        # AC power the battery can actually charge/discharge this slot, bounded by store room.
        headroom_kwh = max(0.0, usable - soc_kwh)
        avail_kwh = max(0.0, soc_kwh - reserve_kwh)
        max_charge_ac = min(model.max_charge_w, headroom_kwh / eta / dh * 1000.0)
        max_discharge_ac = min(model.max_discharge_w, avail_kwh * eta / dh * 1000.0)

        if ps.intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            battery_w = -max_charge_ac  # force-charge at full available power
        elif ps.intent is BatteryIntent.HOLD_RESERVE:
            # Hold charge for a coming peak: never discharge, but soak free solar surplus.
            battery_w = -min(-net, max_charge_ac) if net < 0 else 0.0
        elif ps.intent is BatteryIntent.DISCHARGE_FOR_LOAD:
            battery_w = min(net, max_discharge_ac) if net > 0 else 0.0
        else:  # ALLOW_SELF_CONSUMPTION — track the house: discharge deficit, charge surplus
            if net > 0:
                battery_w = min(net, max_discharge_ac)
            elif net < 0:
                battery_w = -min(-net, max_charge_ac)
            else:
                battery_w = 0.0

        if battery_w < 0:  # charging: store gains less than the AC drawn
            soc_kwh += (-battery_w) * eta * dh / 1000.0
        elif battery_w > 0:  # discharging: store loses more than the AC delivered
            soc_kwh -= battery_w / eta * dh / 1000.0
        soc_kwh = _clamp(soc_kwh, 0.0, usable)

        out.append(ProjectedSlot(
            start=ps.start, intent=ps.intent,
            soc_pct=soc_kwh / usable * 100.0 if usable > 0 else 0.0,
            battery_w=battery_w, grid_w=load - solar - battery_w,
            solar_w=solar, load_w=load,
        ))
    return out
