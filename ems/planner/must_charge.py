"""Optional must-charge before the evening peak (#323 / audit F-02).

Pure planner overlay: adds ``GRID_CHARGE_TO_TARGET`` slots only when enabled and every guard
passes. Never weakens budgets, reserve, or arbitrage economics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ems.domain import BatteryIntent
from ems.planner.adaptive import detect_must_charge_signals
from ems.planner.charge_need import stored_kwh_per_slot
from ems.planner.reason import format_must_charge_slot_reason
from ems.planner.rule_based import late_pack_charge_starts
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.prices import PriceSlot

_H = 0.25
_PEAK_HORIZON = timedelta(hours=12)


def _price_span_incomplete(
    span_start: datetime,
    span_end: datetime,
    price_by: dict[datetime, float],
) -> bool:
    """True when any quarter-hour in [span_start, span_end) lacks a known price."""
    cursor = span_start
    while cursor < span_end:
        if cursor not in price_by:
            return True
        cursor += SLOT
    return False


@dataclass(frozen=True)
class MustChargeConfig:
    enabled: bool = False
    target_soc_pct: float = 30.0
    max_price_eur_per_kwh: float = 0.40
    buffer_pp: float = 10.0
    usable_kwh: float = 10.0
    effective_floor_soc_pct: float = 10.0
    max_charge_w: float = 4000.0
    round_trip_efficiency: float = 0.90
    capability_ready: bool = False
    device_min_target_soc_pct: float | None = None


def clamp_must_charge_target_soc(
    target_pct: float,
    *,
    effective_floor_pct: float,
    device_min_pct: float | None,
) -> float | None:
    """Clamp configured target to reserve/device floors and 0–100 %. None ⇒ do not plan."""
    if device_min_pct is None:
        return None
    floor = max(effective_floor_pct, device_min_pct)
    if target_pct < floor - 1e-6:
        return None
    return max(floor, min(100.0, target_pct))


def apply_must_charge(
    plan: Plan,
    prices: list[PriceSlot],
    now: datetime,
    *,
    soc_pct: float,
    cfg: MustChargeConfig,
) -> Plan:
    """Add precautionary grid charge when triggers fire; otherwise return ``plan`` unchanged."""
    if not cfg.enabled or cfg.usable_kwh <= 0 or cfg.max_charge_w <= 0:
        return plan
    if not cfg.capability_ready or cfg.device_min_target_soc_pct is None:
        return plan
    if soc_pct < 0 or soc_pct > 100:
        return plan

    signals = detect_must_charge_signals(
        plan,
        soc_pct=soc_pct,
        effective_floor_pct=cfg.effective_floor_soc_pct,
        buffer_pp=cfg.buffer_pp,
        now=now,
        horizon=_PEAK_HORIZON,
    )
    if signals is None:
        return plan

    target_soc = clamp_must_charge_target_soc(
        cfg.target_soc_pct,
        effective_floor_pct=cfg.effective_floor_soc_pct,
        device_min_pct=cfg.device_min_target_soc_pct,
    )
    if target_soc is None:
        return plan

    price_by = {p.start: p.eur_per_kwh for p in prices}
    future = [p for p in prices if p.start + SLOT > now]
    if not future:
        return plan

    deadline = signals.peak_deadline
    pre_peak = [p for p in future if p.start < deadline]
    if not pre_peak:
        return plan
    ordered_pre = sorted(pre_peak, key=lambda p: p.start)
    span_end = ordered_pre[-1].start + SLOT
    if _price_span_incomplete(ordered_pre[0].start, span_end, price_by):
        return plan
    if _price_span_incomplete(now, deadline, price_by):
        return plan

    pool = [
        p for p in pre_peak
        if price_by[p.start] <= cfg.max_price_eur_per_kwh + 1e-12
    ]
    if not pool:
        return plan

    usable = cfg.usable_kwh
    reserve_kwh = cfg.effective_floor_soc_pct / 100.0 * usable
    target_kwh = target_soc / 100.0 * usable
    current_kwh = max(0.0, min(100.0, soc_pct)) / 100.0 * usable
    need_dc = max(0.0, target_kwh - max(reserve_kwh, current_kwh))
    if need_dc <= 1e-9:
        return plan

    per_slot_kwh = stored_kwh_per_slot(cfg.max_charge_w, cfg.round_trip_efficiency)
    if per_slot_kwh <= 1e-9:
        return plan
    n_charge = math.ceil(need_dc / per_slot_kwh)
    charge_set = late_pack_charge_starts(
        pool, n_charge,
        price_of=lambda p: price_by[p.start],
        max_buy=cfg.max_price_eur_per_kwh,
    )
    if not charge_set:
        return plan
    if any(s > deadline for s in charge_set):
        return plan

    floor_soc = max(0.0, min(100.0, cfg.effective_floor_soc_pct))
    per_slot_kwh = round(per_slot_kwh, 3)
    out: list[PlanSlot] = []
    changed = False
    for slot in plan.slots:
        if slot.start not in charge_set:
            out.append(slot)
            continue
        if slot.start > deadline:
            out.append(slot)
            continue
        changed = True
        out.append(PlanSlot(
            slot.start,
            BatteryIntent.GRID_CHARGE_TO_TARGET,
            format_must_charge_slot_reason(cfg.max_price_eur_per_kwh),
            target_soc=target_soc,
            target_kwh=per_slot_kwh,
            power_w=cfg.max_charge_w,
            floor_soc=slot.floor_soc if slot.floor_soc is not None else floor_soc,
            deadline=deadline,
            end=slot.end,
        ))

    if not changed:
        return plan
    return replace(
        plan,
        slots=tuple(out),
        target_soc=target_soc,
        deadline=deadline,
    )


