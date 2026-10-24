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
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ems.domain import BatteryIntent
from ems.planner.reason import EVENING_RESERVE_HOLD_REASON
from ems.planner.schedule import SLOT, Plan, PlanSlot

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
    charge_target_soc_pct: float | None = None,
    slot: timedelta = SLOT,
) -> list[ProjectedSlot]:
    """Simulate SoC + grid flow over `plan_slots` (each needs `.start` and `.intent`).

    `charge_target_soc_pct` caps `GRID_CHARGE_TO_TARGET`: the battery buys from the grid only up to
    this SoC (the energy needed to carry the night — SPEC §8: a target SoC derived from required
    kWh), instead of force-charging to full. None = charge to capacity (legacy behaviour).

    Contract: `load_w_by` MUST cover every slot start — a missing load silently reads 0 W and
    under-predicts the drain (the API builds it for every plan slot). `solar_w_by` may be sparse;
    a missing entry means 0 W solar (correct for night/pre-dawn slots)."""
    usable = model.usable_kwh
    reserve_kwh = model.reserve_soc_pct / 100.0 * usable
    eta = math.sqrt(_clamp(model.round_trip_efficiency, 1e-6, 1.0))
    dh = slot.total_seconds() / 3600.0
    soc_kwh = _clamp(start_soc_pct, 0.0, 100.0) / 100.0 * usable
    # Grid-charge ceiling in kWh (enough for the night, no more). None -> full usable capacity.
    target_kwh = (usable if charge_target_soc_pct is None
                  else _clamp(charge_target_soc_pct, 0.0, 100.0) / 100.0 * usable)

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
            # Charge at full power, but only up to the night-carry target (don't over-buy).
            room_to_target_ac = max(0.0, target_kwh - soc_kwh) / eta / dh * 1000.0
            battery_w = -min(max_charge_ac, room_to_target_ac)
        elif ps.intent is BatteryIntent.HOLD_RESERVE:
            # Hold charge for a coming peak: never discharge, but soak free solar surplus.
            battery_w = -min(-net, max_charge_ac) if net < 0 else 0.0
        elif ps.intent is BatteryIntent.EXPORT_FOR_PROFIT:
            # Deliberate export — never soak surplus like vendor AUTO (#312 / F-21 contrast).
            battery_w = min(net, max_discharge_ac) if net > 0 else 0.0
        elif ps.intent is BatteryIntent.DISCHARGE_FOR_LOAD:
            # Peak self-consumption: cover deficit; soak solar surplus like AUTO (#312 / F-21).
            if net > 0:
                battery_w = min(net, max_discharge_ac)
            elif net < 0:
                battery_w = -min(-net, max_charge_ac)
            else:
                battery_w = 0.0
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


def mark_floor_holds(
    plan: Plan,
    projected: list[ProjectedSlot] | None,
    floor_soc: float,
    tol_pp: float = 1.0,
    *,
    start_soc_pct: float | None = None,
) -> Plan:
    """Rewrite DISCHARGE_FOR_LOAD at the SoC floor to HOLD_RESERVE (#312 / audit F-16b).

    Only **fake** discharge: the slot starts at or below the reserve floor and the forward
    projection already has ``battery_w == 0`` (vendor cannot discharge). Do not mark slots that
    still project real discharge or that start above the floor — that would plan ``HOLD_RESERVE``
    while ``resolve_floor_anti_flap`` keeps ``AUTO`` and cause an extra IDLE write (#312 AC5).

    ``tol_pp`` is retained for call-site compatibility; marking no longer keys off end-of-slot SoC.
    Pure: no I/O, no device writes.
    """
    if projected is None or not plan.slots or len(projected) != len(plan.slots):
        return plan
    _ = tol_pp  # reserved for API compatibility (#312); marking uses slot-start SoC only.
    floor = float(floor_soc)
    out: list[PlanSlot] = []
    changed = False
    for i, (slot, proj) in enumerate(zip(plan.slots, projected, strict=True)):
        if start_soc_pct is not None:
            slot_start_soc = start_soc_pct if i == 0 else projected[i - 1].soc_pct
        else:
            slot_start_soc = None
        if (
            slot.intent is BatteryIntent.DISCHARGE_FOR_LOAD
            and proj.battery_w == 0.0
            and slot_start_soc is not None
            and slot_start_soc <= floor
        ):
            reason = f"Op de batterijvloer ({floor:.0f}%) — huis op net"
            out.append(PlanSlot(
                slot.start, BatteryIntent.HOLD_RESERVE, reason,
                target_soc=slot.target_soc, target_kwh=slot.target_kwh,
                power_w=slot.power_w,
                floor_soc=slot.floor_soc if slot.floor_soc is not None else floor,
                deadline=slot.deadline, end=slot.end,
            ))
            changed = True
        else:
            out.append(slot)
    if not changed:
        return plan
    return Plan(
        created_at=plan.created_at, slots=tuple(out), strategy=plan.strategy,
        target_soc=plan.target_soc, deadline=plan.deadline, id=plan.id,
        version=plan.version, input_snapshot=plan.input_snapshot,
        planner_mode=plan.planner_mode, buy_pool=plan.buy_pool,
        solar_basis=plan.solar_basis,
    )


def _evening_clusters(slots: Iterable) -> list[list]:
    """Contiguous ``DISCHARGE_FOR_LOAD`` runs. A time gap starts a new run."""
    groups: list[list] = []
    current: list = []
    for slot in slots:
        if slot.intent is BatteryIntent.DISCHARGE_FOR_LOAD:
            if current and slot.start != current[-1].start + SLOT:
                groups.append(current)
                current = [slot]
            else:
                current.append(slot)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def evening_discharge_slots(slots: Iterable, site_tz) -> tuple[tuple, datetime | None]:
    """Evening discharge cluster: a run whose local start is at or after 15:00.

    Returns ``(slots, evening_start)``. ``evening_start`` is the first slot of the
    earliest such run. Runs that begin before 15:00 local stay morning peaks.
    """
    evening: list = []
    for group in _evening_clusters(slots):
        local = group[0].start.astimezone(site_tz)
        if (local.hour, local.minute) >= (15, 0):
            evening.extend(group)
    if not evening:
        return (), None
    return tuple(evening), evening[0].start


def _deficit_dc_kwh(
    load_w: float,
    solar_w: float,
    *,
    eta: float,
    max_discharge_w: float,
    dh: float,
) -> float:
    """DC kWh the battery must give up to cover this slot's house deficit."""
    net = load_w - solar_w
    if net <= 0.0 or eta <= 0.0 or dh <= 0.0 or max_discharge_w <= 0.0:
        return 0.0
    ac_w = min(net, max_discharge_w)
    return ac_w * dh / 1000.0 / eta


def evening_reserve_kwh(
    slots: Iterable,
    *,
    load_w_by: dict[datetime, float],
    usable_kwh: float,
    reserve_soc_pct: float,
    max_discharge_w: float,
    round_trip_efficiency: float,
    site_tz,
    solar_w_by: dict[datetime, float] | None = None,
) -> tuple[float, datetime | None]:
    """DC ``evening_reserve_kwh`` and the local evening-cluster start (#326).

    The reserve is the deficit of the evening discharge slots, capped at the energy
    above the reserve floor (``usable − floor``). No evening cluster → ``(0, None)``.
    """
    solar_by = solar_w_by or {}
    evening, evening_start = evening_discharge_slots(slots, site_tz)
    if not evening or usable_kwh <= 0.0:
        return 0.0, None
    eta = math.sqrt(_clamp(round_trip_efficiency, 1e-6, 1.0))
    total = 0.0
    for slot in evening:
        total += _deficit_dc_kwh(
            load_w_by.get(slot.start, 0.0),
            solar_by.get(slot.start, 0.0),
            eta=eta, max_discharge_w=max_discharge_w, dh=SLOT_HOURS,
        )
    floor_kwh = reserve_soc_pct / 100.0 * usable_kwh
    cap = max(0.0, usable_kwh - floor_kwh)
    return min(total, cap), evening_start


def apply_evening_reserve(
    plan: Plan,
    *,
    start_soc_pct: float,
    load_w_by: dict[datetime, float] | None,
    usable_kwh: float,
    reserve_soc_pct: float,
    max_charge_w: float,
    max_discharge_w: float,
    round_trip_efficiency: float,
    site_tz,
    solar_w_by: dict[datetime, float] | None = None,
) -> Plan:
    """Hold morning discharge once it would spend the evening reserve (#326).

    Only ``DISCHARGE_FOR_LOAD`` slots before ``evening_start`` can become
    ``HOLD_RESERVE``. Charge slots, the horizon and the buy size are untouched.
    Returns the same plan object when nothing changes.
    """
    if not plan.slots or load_w_by is None:
        return plan
    solar_by = solar_w_by or {}
    need, evening_start = evening_reserve_kwh(
        plan.slots, load_w_by=load_w_by, usable_kwh=usable_kwh,
        reserve_soc_pct=reserve_soc_pct, max_discharge_w=max_discharge_w,
        round_trip_efficiency=round_trip_efficiency, site_tz=site_tz,
        solar_w_by=solar_by,
    )
    if evening_start is None or need <= 1e-12:
        return plan
    usable = float(usable_kwh)
    if usable <= 0.0:
        return plan
    floor_kwh = reserve_soc_pct / 100.0 * usable
    eta = math.sqrt(_clamp(round_trip_efficiency, 1e-6, 1.0))
    dh = SLOT_HOURS
    soc_kwh = _clamp(start_soc_pct, 0.0, 100.0) / 100.0 * usable
    target_kwh = (
        usable if plan.target_soc is None
        else _clamp(plan.target_soc, 0.0, 100.0) / 100.0 * usable
    )
    out: list[PlanSlot] = []
    changed = False
    for slot in plan.slots:
        solar = solar_by.get(slot.start, 0.0)
        load = load_w_by.get(slot.start, 0.0)
        net = load - solar
        headroom_kwh = max(0.0, usable - soc_kwh)
        avail_kwh = max(0.0, soc_kwh - floor_kwh)
        max_charge_ac = min(max_charge_w, headroom_kwh / eta / dh * 1000.0)
        max_discharge_ac = min(max_discharge_w, avail_kwh * eta / dh * 1000.0)
        hold_this = False
        if (
            slot.intent is BatteryIntent.DISCHARGE_FOR_LOAD
            and slot.start < evening_start
            and net > 0.0
        ):
            discharge_w = min(net, max_discharge_ac)
            slot_dc = discharge_w / eta * dh / 1000.0
            if soc_kwh - slot_dc < floor_kwh + need:
                hold_this = True
                battery_w = 0.0
            else:
                battery_w = discharge_w
        elif slot.intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            room_to_target_ac = max(0.0, target_kwh - soc_kwh) / eta / dh * 1000.0
            battery_w = -min(max_charge_ac, room_to_target_ac)
        elif slot.intent is BatteryIntent.HOLD_RESERVE:
            battery_w = -min(-net, max_charge_ac) if net < 0.0 else 0.0
        elif slot.intent is BatteryIntent.EXPORT_FOR_PROFIT:
            battery_w = min(net, max_discharge_ac) if net > 0.0 else 0.0
        elif net > 0.0:
            battery_w = min(net, max_discharge_ac)
        elif net < 0.0:
            battery_w = -min(-net, max_charge_ac)
        else:
            battery_w = 0.0
        if battery_w < 0.0:
            soc_kwh += (-battery_w) * eta * dh / 1000.0
        elif battery_w > 0.0:
            soc_kwh -= battery_w / eta * dh / 1000.0
        soc_kwh = _clamp(soc_kwh, 0.0, usable)
        if hold_this:
            out.append(replace(
                slot, intent=BatteryIntent.HOLD_RESERVE,
                reason=EVENING_RESERVE_HOLD_REASON,
            ))
            changed = True
        else:
            out.append(slot)
    if not changed:
        return plan
    return replace(plan, slots=tuple(out))
