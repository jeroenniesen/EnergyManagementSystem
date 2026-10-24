"""Adaptive demand-aware charging (SPEC §8.3 "size to serve the load, not a fixed ceiling").

One algorithm for both seasons. It sizes the battery to the energy it should actually deliver over
the coming evening + night — computed from the FORECAST load minus the forecast solar — rather than
a fixed night-carry constant (which the backtest showed under-sizes on dull days and leaves the
battery draining mid-peak at peak price). It then grid-charges only the shortfall solar won't cover,
in the cost-minimal contiguous slots *before* the expensive window (tie → later; cheapest-n
if no contiguous window fits), so the battery is full going into the peak and
shaves it. Risk-aware: the solar it counts on to fill the battery is the EXPECTED (P50) forecast
scaled by `solar_confidence` (0..1) — a single tunable conservatism dial. (This replaced an earlier
fixed P10 = 0.6×P50 haircut, which over-bought; see `docs/charging-algorithm-research.md`.)

Pure + unit-tested. The caller supplies the expected load per slot (the learned profile).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner import economics
from ems.planner.budgets import ac_grid_kwh_per_slot, clamp_cycle_budget, clamp_grid_charge
from ems.planner.buy_pool import build_buy_pool_info
from ems.planner.reason import is_must_charge_slot
from ems.planner.rule_based import late_pack_charge_starts
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
    # Fraction of the EXPECTED (P50) forecast we count on filling the battery (0..1); see summer.py.
    solar_confidence: float = 0.8
    horizon_slots: int = 96
    negative_price_soak: bool = False  # opt-in: charge on sub-zero slots (paid to consume)
    max_daily_grid_charge_kwh: float = 12.0  # #317 hard daily grid-buy cap
    already_bought_today_kwh: float | None = 0.0
    max_cycles_per_day: float = 1.5
    charged_today_kwh: float | None = 0.0
    discharged_today_kwh: float | None = 0.0
    site_tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Amsterdam"))
    must_charge_enabled: bool = False
    must_charge_target_soc: float = 30.0
    must_charge_max_price_eur_per_kwh: float = 0.40
    must_charge_buffer_pp: float = 10.0
    must_charge_capability_ready: bool = False
    must_charge_device_min_target_soc: float | None = None
    # Resident override (#322): charge to this SoC before this deadline, via the must-charge
    # path. None means no override — the automatic #323 trigger is unchanged.
    resident_must_charge_target_soc: float | None = None
    resident_must_charge_deadline: datetime | None = None
    max_buy_eur_per_kwh: float = 0.0  # #324 / F-15; 0 = automatic
    # #330 / F-18. auto sizes on min(confidence×P50, P10) when slots carry real_p10.
    commit_solar_basis: str = "auto"


def committed_solar_w(
    p50_w: float,
    p10_w: float,
    confidence: float,
    *,
    commit_solar_basis: str = "auto",
    real_p10: bool = False,
) -> float:
    """Watts counted when sizing a committed grid charge (#330 / F-18).

    ``auto`` plus a delivery stamped with real P10 is ``min(confidence × P50, P10)``,
    never above today's confidence × P50. Every other case, including an unknown
    forecast, is exactly ``confidence × P50``.
    """
    scaled = confidence * p50_w
    if commit_solar_basis == "auto" and real_p10:
        return min(scaled, max(0.0, p10_w))
    return scaled


def commitment_solar_basis(
    commit_solar_basis: str = "auto",
    real_p10: bool = False,
) -> str | None:
    """``p10`` when auto used a real P10, ``p50`` when that mode is explicit, else None.

    None keeps plans that did not choose a P10 path identical to the pre-#330 default.
    """
    if commit_solar_basis == "auto" and real_p10:
        return "p10"
    if commit_solar_basis == "p50":
        return "p50"
    return None


def _delivery_has_real_p10(forecast: list[ForecastSlot]) -> bool:
    """True when this delivery was stamped real P10 on every slot. Empty stays false."""
    return bool(forecast) and all(bool(getattr(slot, "real_p10", False)) for slot in forecast)


@dataclass(frozen=True)
class MustChargeTriggerSignals:
    shortfall_kwh: float
    peak_deadline: datetime


_EVENING_PEAK_HOURS = range(17, 22)


def detect_must_charge_signals(
    plan: Plan,
    *,
    soc_pct: float,
    effective_floor_pct: float,
    buffer_pp: float,
    now: datetime,
    horizon: timedelta,
    site_tz: ZoneInfo | None = None,
) -> MustChargeTriggerSignals | None:
    """Shortfall + empty/insufficient arbitrage pool, low SoC, and a peak within ``horizon``."""
    pool_info = plan.buy_pool
    if pool_info is None or pool_info.shortfall_kwh <= 1e-9:
        return None
    arbitrage_charge = [
        s for s in plan.slots
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
        and not is_must_charge_slot(s)
    ]
    insufficient = (
        pool_info.state == "empty"
        or (pool_info.shortfall_kwh > 1e-9 and not arbitrage_charge)
        or (pool_info.pool_size > 0 and len(arbitrage_charge) == 0
            and pool_info.state != "not_needed")
    )
    if not insufficient:
        return None
    if soc_pct >= effective_floor_pct + max(0.0, buffer_pp) - 1e-6:
        return None
    tz = site_tz or ZoneInfo("Europe/Amsterdam")
    peaks = [
        s.start for s in plan.slots
        if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD
        and s.start > now
        and s.start <= now + horizon
        and s.start.astimezone(tz).hour in _EVENING_PEAK_HOURS
    ]
    if not peaks:
        return None
    deadline = min(peaks)
    return MustChargeTriggerSignals(
        shortfall_kwh=pool_info.shortfall_kwh,
        peak_deadline=deadline,
    )


def plan_adaptive(
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    now: datetime,
    *,
    soc_pct: float,
    load_w_by: dict[datetime, float],
    cfg: AdaptiveConfig,
) -> Plan:
    """Demand-aware charge plan (see the module docstring). With `cfg.negative_price_soak` (opt-in,
    default OFF) every sub-zero-priced slot is additionally turned into a charge slot afterwards —
    you are *paid* to consume — even outside a normal cheap window (`_soak_negative`)."""
    plan = _plan_adaptive(prices, forecast, now, soc_pct=soc_pct, load_w_by=load_w_by, cfg=cfg)
    if cfg.negative_price_soak:
        plan = _soak_negative(plan, prices, cfg)
    capped = clamp_grid_charge(
        plan, cfg.already_bought_today_kwh, cfg.max_daily_grid_charge_kwh,
        ac_grid_kwh_per_slot(cfg.max_charge_w),
        tz=cfg.site_tz,
    )
    return clamp_cycle_budget(
        capped, cfg.charged_today_kwh, cfg.discharged_today_kwh,
        cfg.max_cycles_per_day, cfg.usable_kwh,
        round_trip_efficiency=cfg.round_trip_efficiency,
        charge_fallback_w=cfg.max_charge_w, tz=cfg.site_tz,
    )


def _plan_adaptive(
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
    confidence = max(0.0, min(1.0, cfg.solar_confidence))
    real_p10 = _delivery_has_real_p10(forecast)
    basis = commitment_solar_basis(cfg.commit_solar_basis, real_p10)

    def counted_w(start: datetime) -> float:
        return committed_solar_w(
            p50.get(start, 0.0), p10.get(start, 0.0), confidence,
            commit_solar_basis=cfg.commit_solar_basis,
            real_p10=real_p10,
        )
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    usable = cfg.usable_kwh
    reserve_kwh = cfg.reserve_soc_pct / 100.0 * usable
    avail_now_kwh = max(0.0, soc_pct / 100.0 * usable - reserve_kwh)

    # The price at which charging starts to pay (cheapest-quartile price + round-trip losses).
    ranked = sorted(p.eur_per_kwh for p in future)
    charge_price = ranked[len(ranked) // 4]
    breakeven = economics.breakeven(
        charge_price,
        round_trip_efficiency=cfg.round_trip_efficiency,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
    )

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
    # Solar we count on flowing into the battery from daytime surplus: confidence × P50,
    # or min(that, P10) when auto and the delivered source has a real P10 (#330).
    # Otherwise exactly confidence × P50 (was a hard P10 = 0.6×P50 haircut).
    solar_to_batt_kwh = sum(
        max(0.0, counted_w(p.start) - load_w_by.get(p.start, 0.0)) * _DH / 1000.0
        * eta
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
    max_buy_eur = breakeven
    auto_max_buy_eur = breakeven
    max_buy_eur = economics.apply_max_buy_cap(max_buy_eur, cfg.max_buy_eur_per_kwh)
    if discharge_set:
        # Shaving a peak: buy only slots that still undercut the cheapest peak after losses,
        # wear and risk. The old raw `peak_min` gate ignored that and over-bought.
        peak_price = min(p.eur_per_kwh for p in future if p.start in discharge_set)
        max_buy_eur = economics.max_buy_for_planner_config(peak_price, cfg)
        auto_max_buy_eur = max_buy_eur
        max_buy_eur = economics.apply_max_buy_cap(max_buy_eur, cfg.max_buy_eur_per_kwh)
        pool = [p for p in future if p.start < last_need and p.eur_per_kwh <= max_buy_eur]
        pre_peak = [p for p in future if p.start < last_need]
    else:
        # No expensive peak ahead: only charge if genuinely cheap, to avoid pointless cycling.
        pool = [p for p in future if p.eur_per_kwh <= max_buy_eur]
        pre_peak = list(future)
    # Pool is already capped and still chronological. Late-pack (same helper as winter) picks the
    # cost-minimal contiguous window; max_buy is the pool's own ceiling so the existing cap is
    # unchanged. No full window → cheapest n, and |charge_set| stays <= n_charge.
    charge_set = late_pack_charge_starts(
        pool, n_charge,
        price_of=lambda p: p.eur_per_kwh,
        max_buy=max((p.eur_per_kwh for p in pool), default=0.0),
    )
    buy_pool = build_buy_pool_info(
        pre_peak=pre_peak,
        pool=pool,
        n_charge=n_charge,
        shortfall_kwh=shortfall_kwh,
        max_buy_eur_per_kwh=max_buy_eur,
        auto_max_buy_eur_per_kwh=auto_max_buy_eur,
        resident_cap_eur_per_kwh=cfg.max_buy_eur_per_kwh,
        price_of=lambda p: p.eur_per_kwh,
    )

    # The SoC the plan should reach (reserve + the demand it must cover), and by when.
    target_soc_pct = (min(100.0, (reserve_kwh + coverable_kwh) / usable * 100.0)
                      if usable > 0 else 0.0)
    floor_soc = max(0.0, min(100.0, cfg.reserve_soc_pct))

    out: list[PlanSlot] = []
    planned_kwh = max(0.0, min(100.0, soc_pct)) / 100.0 * usable
    for p in future:
        solar = p50.get(p.start, 0.0)
        load = load_w_by.get(p.start, 0.0)
        deficit_w = max(0.0, load - solar)
        deficit_kwh = deficit_w * _DH / 1000.0 / eta
        would_breach_reserve = deficit_w > 0 and planned_kwh - deficit_kwh < reserve_kwh - 1e-9
        if p.start in charge_set:
            intent = BatteryIntent.GRID_CHARGE_TO_TARGET
            reason = f"charge: cheap €{p.eur_per_kwh:.2f}/kWh to cover the coming peak + night"
            out.append(PlanSlot(
                p.start, intent, reason, target_soc=target_soc_pct,
                target_kwh=round(per_slot_kwh, 3), power_w=cfg.max_charge_w,
                floor_soc=floor_soc, deadline=last_need,
            ))
            target_kwh = target_soc_pct / 100.0 * usable
            planned_kwh = min(usable, target_kwh, planned_kwh + per_slot_kwh)
            continue
        if would_breach_reserve:
            intent = BatteryIntent.HOLD_RESERVE
            reason = "hold reserve: serving this slot would cross the reserve floor"
            out.append(PlanSlot(p.start, intent, reason, target_soc=target_soc_pct,
                                floor_soc=floor_soc, deadline=last_need))
            continue
        if p.start in discharge_set:
            intent = BatteryIntent.DISCHARGE_FOR_LOAD
            reason = f"shave the €{p.eur_per_kwh:.2f}/kWh peak from the battery"
            out.append(PlanSlot(p.start, intent, reason, power_w=round(max(0.0, net_w(p)), 1),
                                floor_soc=floor_soc, deadline=last_need))
            planned_kwh = max(reserve_kwh, planned_kwh - deficit_kwh)
            continue
        if solar > load:
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = f"solar-first: charging from your panels ({solar:.0f} W)"
            planned_kwh = min(usable, planned_kwh + (solar - load) * _DH / 1000.0 * eta)
        else:
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = "running the house on the battery"
            planned_kwh = max(reserve_kwh, planned_kwh - deficit_kwh)
        out.append(PlanSlot(p.start, intent, reason, target_soc=target_soc_pct, floor_soc=floor_soc,
                            deadline=last_need))
    # No season label here: this planner serves both seasons. build_plan stamps the resolved
    # strategy; a direct caller gets an honest None rather than a misleading "summer".
    return Plan(created_at=now, slots=tuple(out), strategy=None, target_soc=target_soc_pct,
                deadline=last_need, buy_pool=buy_pool, solar_basis=basis)


def _soak_negative(plan: Plan, prices: list[PriceSlot], cfg: AdaptiveConfig) -> Plan:
    """Negative-price soak (`negative_price_soak`, opt-in, default OFF). When a slot's price is
    below €0 you are *paid* to consume, so rewrite it to a full grid-charge — up to battery
    headroom, even outside a normal cheap window. A sub-zero slot can therefore never remain a
    discharge slot. Every other slot is left untouched (flag off ⇒ byte-identical plan)."""
    price_by = {p.start: p.eur_per_kwh for p in prices}
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    per_slot_kwh = round(cfg.max_charge_w * _DH / 1000.0 * eta, 3)
    floor = max(0.0, min(100.0, cfg.reserve_soc_pct))
    out = tuple(
        PlanSlot(
            s.start, BatteryIntent.GRID_CHARGE_TO_TARGET,
            f"charge: price €{price_by[s.start]:.2f}/kWh below €0 — you are paid to charge",
            target_soc=100.0, target_kwh=per_slot_kwh, power_w=cfg.max_charge_w,
            floor_soc=floor, deadline=s.deadline,
        )
        if price_by.get(s.start, 0.0) < 0.0 else s
        for s in plan.slots
    )
    return Plan(created_at=plan.created_at, slots=out, strategy=plan.strategy,
                target_soc=plan.target_soc, deadline=plan.deadline, buy_pool=plan.buy_pool,
                solar_basis=plan.solar_basis)
