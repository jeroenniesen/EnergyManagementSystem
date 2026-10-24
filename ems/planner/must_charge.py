"""Optional must-charge before the evening peak (#323 / audit F-02).

Pure planner overlay: adds ``GRID_CHARGE_TO_TARGET`` slots only when enabled and every guard
passes. Never weakens budgets, reserve, or arbitrage economics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig, detect_must_charge_signals
from ems.planner.charge_need import stored_kwh_per_slot
from ems.planner.reason import (
    format_must_charge_slot_reason,
    format_resident_must_charge_reason,
    is_resident_must_charge_slot,
)
from ems.planner.rule_based import late_pack_charge_starts
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.prices import PriceSlot

_H = 0.25
_PEAK_HORIZON = timedelta(hours=12)


def _quarter_floor_utc(dt: datetime) -> datetime:
    """Floor to the UTC quarter-hour containing ``dt`` (price slot alignment)."""
    utc = dt.astimezone(UTC)
    minute = (utc.minute // 15) * 15
    return utc.replace(minute=minute, second=0, microsecond=0)


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
    site_tz: ZoneInfo = ZoneInfo("Europe/Amsterdam")


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
        site_tz=cfg.site_tz,
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
    if _price_span_incomplete(_quarter_floor_utc(now), deadline, price_by):
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


# Resident override (#322 / F-41). Same slot packer, price cap and battery limits as the
# automatic path above; the arbitrage-pool trigger is not required. Daily ceiling and the
# EFC budget still run in strategy.build_plan after this overlay.
GEEN_STURING = "geen sturing"
BATTERY_LIMIT_REASON = "Batterijvermogen: doel niet haalbaar vóór de deadline"


def price_cap_unreachable_reason(max_price_eur_per_kwh: float) -> str:
    return f"Prijsplafond: niet genoeg slots onder €{max_price_eur_per_kwh:.2f}/kWh"


@dataclass(frozen=True)
class ResidentSelection:
    starts: frozenset[datetime]
    reason: str | None
    target_soc: float
    deadline: datetime


def _resident_configured(adaptive: AdaptiveConfig) -> tuple[float, datetime] | None:
    target = adaptive.resident_must_charge_target_soc
    deadline = adaptive.resident_must_charge_deadline
    if target is None or deadline is None:
        return None
    return float(target), deadline


def select_resident_charge(
    prices: list[PriceSlot],
    now: datetime,
    *,
    soc_pct: float,
    cfg: MustChargeConfig,
    deadline: datetime,
) -> ResidentSelection:
    """Cheapest pre-deadline slots that fit the price cap and the battery, before daily clamps."""
    empty = ResidentSelection(frozenset(), GEEN_STURING, cfg.target_soc_pct, deadline)
    if deadline <= now:
        return ResidentSelection(frozenset(), None, cfg.target_soc_pct, deadline)
    if not cfg.capability_ready or cfg.device_min_target_soc_pct is None:
        return empty
    if cfg.usable_kwh <= 0 or cfg.max_charge_w <= 0:
        return empty
    if soc_pct < 0 or soc_pct > 100:
        return empty

    target_soc = clamp_must_charge_target_soc(
        cfg.target_soc_pct,
        effective_floor_pct=cfg.effective_floor_soc_pct,
        device_min_pct=cfg.device_min_target_soc_pct,
    )
    if target_soc is None:
        return empty

    price_by = {p.start: p.eur_per_kwh for p in prices}
    future = [p for p in prices if p.start + SLOT > now and p.start < deadline]
    if not future:
        return ResidentSelection(
            frozenset(), BATTERY_LIMIT_REASON, target_soc, deadline,
        )
    ordered = sorted(future, key=lambda p: p.start)
    if _price_span_incomplete(ordered[0].start, ordered[-1].start + SLOT, price_by):
        return empty
    if _price_span_incomplete(_quarter_floor_utc(now), deadline, price_by):
        return empty

    usable = cfg.usable_kwh
    reserve_kwh = cfg.effective_floor_soc_pct / 100.0 * usable
    target_kwh = target_soc / 100.0 * usable
    current_kwh = max(0.0, min(100.0, soc_pct)) / 100.0 * usable
    need_dc = max(0.0, target_kwh - max(reserve_kwh, current_kwh))
    if need_dc <= 1e-9:
        return ResidentSelection(frozenset(), None, target_soc, deadline)

    per_slot_kwh = stored_kwh_per_slot(cfg.max_charge_w, cfg.round_trip_efficiency)
    if per_slot_kwh <= 1e-9:
        return empty
    n_charge = math.ceil(need_dc / per_slot_kwh)
    pool = [
        p for p in ordered
        if price_by[p.start] <= cfg.max_price_eur_per_kwh + 1e-12
    ]
    if not pool:
        return ResidentSelection(
            frozenset(),
            price_cap_unreachable_reason(cfg.max_price_eur_per_kwh),
            target_soc,
            deadline,
        )
    charge_set = late_pack_charge_starts(
        ordered, n_charge,
        price_of=lambda p: price_by[p.start],
        max_buy=cfg.max_price_eur_per_kwh,
    )
    charge_set = {start for start in charge_set if start < deadline}
    reason = None
    if len(charge_set) < n_charge:
        if len(pool) < len(ordered):
            reason = price_cap_unreachable_reason(cfg.max_price_eur_per_kwh)
        else:
            reason = BATTERY_LIMIT_REASON
    return ResidentSelection(frozenset(charge_set), reason, target_soc, deadline)


def _resident_cfg(adaptive: AdaptiveConfig, target_soc: float) -> MustChargeConfig:
    return MustChargeConfig(
        enabled=True,
        target_soc_pct=target_soc,
        max_price_eur_per_kwh=adaptive.must_charge_max_price_eur_per_kwh,
        buffer_pp=adaptive.must_charge_buffer_pp,
        usable_kwh=adaptive.usable_kwh,
        effective_floor_soc_pct=adaptive.reserve_soc_pct,
        max_charge_w=adaptive.max_charge_w,
        round_trip_efficiency=adaptive.round_trip_efficiency,
        capability_ready=adaptive.must_charge_capability_ready,
        device_min_target_soc_pct=adaptive.must_charge_device_min_target_soc,
        site_tz=adaptive.site_tz,
    )


def apply_resident_overlay(
    plan: Plan,
    prices: list[PriceSlot],
    now: datetime,
    *,
    soc_pct: float,
    adaptive_cfg: AdaptiveConfig | None,
) -> Plan:
    """GRID_CHARGE in the cheapest slots before the resident deadline. No-op without an override."""
    if adaptive_cfg is None:
        return plan
    requested = _resident_configured(adaptive_cfg)
    if requested is None:
        return plan
    target, deadline = requested
    cfg = _resident_cfg(adaptive_cfg, target)
    selected = select_resident_charge(
        prices, now, soc_pct=soc_pct, cfg=cfg, deadline=deadline,
    )
    if not selected.starts:
        return plan
    hhmm = deadline.astimezone(cfg.site_tz).strftime("%H:%M")
    reason = format_resident_must_charge_reason(
        selected.target_soc, hhmm, cfg.max_price_eur_per_kwh,
    )
    per_slot_kwh = round(
        stored_kwh_per_slot(cfg.max_charge_w, cfg.round_trip_efficiency), 3,
    )
    floor_soc = max(0.0, min(100.0, cfg.effective_floor_soc_pct))
    out: list[PlanSlot] = []
    changed = False
    for slot in plan.slots:
        if slot.start not in selected.starts or slot.start >= deadline:
            out.append(slot)
            continue
        changed = True
        out.append(PlanSlot(
            slot.start,
            BatteryIntent.GRID_CHARGE_TO_TARGET,
            reason,
            target_soc=selected.target_soc,
            target_kwh=per_slot_kwh,
            power_w=cfg.max_charge_w,
            floor_soc=slot.floor_soc if slot.floor_soc is not None else floor_soc,
            deadline=deadline,
            end=slot.end,
        ))
    if not changed:
        return plan
    return replace(plan, slots=tuple(out))


def explain_resident_override(
    plan: Plan,
    prices: list[PriceSlot],
    now: datetime,
    *,
    soc_pct: float,
    adaptive_cfg: AdaptiveConfig | None,
) -> str | None:
    """Why the resident target cannot be fully planned, after ceiling and cycle clamps."""
    if adaptive_cfg is None:
        return None
    requested = _resident_configured(adaptive_cfg)
    if requested is None:
        return None
    target, deadline = requested
    cfg = _resident_cfg(adaptive_cfg, target)
    selected = select_resident_charge(
        prices, now, soc_pct=soc_pct, cfg=cfg, deadline=deadline,
    )
    by_start = {slot.start: slot for slot in plan.slots}
    for start in sorted(selected.starts):
        slot = by_start.get(start)
        if slot is None:
            continue
        if (
            slot.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
            and is_resident_must_charge_slot(slot)
        ):
            continue
        text = slot.reason or ""
        if text.startswith(("Dagplafond", "Cycle-budget", "Dagteller")):
            return text
    return selected.reason


