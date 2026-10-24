"""#323 optional must-charge before the evening peak (audit F-02)."""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.control.mode_controller import Lifecycle, ModeController
from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.budgets import clamp_grid_charge
from ems.planner.economics import apply_daily_savings_floor
from ems.planner.must_charge import MustChargeConfig, apply_must_charge
from ems.planner.reason import MUST_CHARGE_REASON, build_decision_reason, is_must_charge_slot
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.strategy import (
    _apply_savings_floor,
    _clamp_shared_cycles,
    _daily_cap_inputs,
    build_plan,
    maybe_apply_trading,
)
from ems.planner.summer import SummerConfig
from ems.planner.validator import validate_plan
from ems.sources.battery import MockBatteryDriver
from ems.sources.prices import PriceSlot

T0 = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
_AMS = ZoneInfo("Europe/Amsterdam")
_SUMMER = SummerConfig(usable_kwh=10.8, target_soc_pct=80.0)


def _adaptive(**kw) -> AdaptiveConfig:
    base = dict(
        usable_kwh=10.8,
        reserve_soc_pct=10.0,
        round_trip_efficiency=0.90,
        max_charge_w=4000.0,
        must_charge_capability_ready=True,
        must_charge_device_min_target_soc=5.0,
    )
    base.update(kw)
    return AdaptiveConfig(**base)


def _empty_pool_fixture(now: datetime = T0):
    local = now.astimezone(_AMS)
    evening = local.replace(hour=17, minute=0, second=0, microsecond=0)
    if evening <= local:
        evening += timedelta(days=1)
    peak_start = evening.astimezone(UTC)
    n_pre = max(8, int((peak_start - now) // SLOT))
    prices = (
        [PriceSlot(now + i * SLOT, 0.37) for i in range(n_pre)]
        + [PriceSlot(peak_start + i * SLOT, 0.45) for i in range(8)]
        + [PriceSlot(peak_start + 8 * SLOT + i * SLOT, 0.05) for i in range(12)]
    )
    load = {p.start: (3500.0 if p.eur_per_kwh >= 0.44 else 500.0) for p in prices}
    return prices, load


def _final_soc_pct(
    slot_starts: list[datetime],
    load: dict[datetime, float],
    solar: dict[datetime, float],
    price_by: dict[datetime, float],
    *,
    cfg,
    start_soc: float,
    intents: dict[datetime, BatteryIntent],
    target_soc: float | None,
) -> float:
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    usable = cfg.usable_kwh
    reserve_kwh = max(0.0, min(100.0, cfg.min_reserve_soc)) / 100.0 * usable
    soc_kwh = max(0.0, min(100.0, start_soc)) / 100.0 * usable
    target_kwh = (
        usable if target_soc is None
        else max(0.0, min(100.0, target_soc)) / 100.0 * usable
    )
    for slot in slot_starts:
        solar_w = solar.get(slot, 0.0)
        load_w = load.get(slot, 0.0)
        net = load_w - solar_w
        headroom_kwh = max(0.0, usable - soc_kwh)
        avail_kwh = max(0.0, soc_kwh - reserve_kwh)
        max_charge_ac = min(cfg.max_charge_w, headroom_kwh / eta / 0.25 * 1000.0)
        max_discharge_ac = min(cfg.max_discharge_w, avail_kwh * eta / 0.25 * 1000.0)
        intent = intents.get(slot, BatteryIntent.ALLOW_SELF_CONSUMPTION)
        if intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            room_to_target_ac = max(0.0, target_kwh - soc_kwh) / eta / 0.25 * 1000.0
            battery_w = -min(max_charge_ac, room_to_target_ac)
        elif intent is BatteryIntent.HOLD_RESERVE:
            battery_w = 0.0
        elif net > 0:
            battery_w = min(net, max_discharge_ac)
        elif net < 0:
            battery_w = -min(-net, max_charge_ac)
        else:
            battery_w = 0.0
        if battery_w < 0:
            soc_kwh += (-battery_w) * eta * 0.25 / 1000.0
        elif battery_w > 0:
            soc_kwh -= battery_w / eta * 0.25 / 1000.0
        soc_kwh = max(0.0, min(usable, soc_kwh))
    return soc_kwh / usable * 100.0


def _slot_tuple(plan: Plan):
    return tuple(
        (
            s.start, s.intent, s.reason, s.target_soc, s.target_kwh, s.power_w,
            s.floor_soc, s.deadline, s.end,
        )
        for s in plan.slots
    )


def _build_plan_without_must_charge(
    *,
    prices: list[PriceSlot],
    now: datetime,
    soc_pct: float,
    winter_cfg: PlannerConfig,
    summer_cfg: SummerConfig,
    load_w_by: dict[datetime, float],
    adaptive_cfg: AdaptiveConfig,
) -> Plan:
    """Seasonal pipeline as before the must-charge overlay (for golden byte identity)."""
    plan = plan_rule_based(
        prices, now, winter_cfg, soc_pct=soc_pct, load_w_by=load_w_by,
        usable_kwh=adaptive_cfg.usable_kwh, reserve_soc_pct=adaptive_cfg.reserve_soc_pct,
        max_charge_w=adaptive_cfg.max_charge_w,
    )
    plan = replace(plan, strategy="winter")
    plan = maybe_apply_trading(
        plan, prices, now, None, soc_pct=soc_pct, load_w_by=load_w_by,
    )
    bought, cap, per_slot, site_tz = _daily_cap_inputs(
        "winter", winter_cfg, summer_cfg, adaptive_cfg, load_w_by,
    )
    plan = clamp_grid_charge(plan, bought, cap, per_slot, tz=site_tz)
    plan = _clamp_shared_cycles(
        plan, "winter", winter_cfg, summer_cfg, adaptive_cfg, load_w_by,
    )
    bought, cap, per_slot, site_tz = _daily_cap_inputs(
        "winter", winter_cfg, summer_cfg, adaptive_cfg, load_w_by,
    )
    plan = clamp_grid_charge(plan, bought, cap, per_slot, tz=site_tz)
    plan = _clamp_shared_cycles(
        plan, "winter", winter_cfg, summer_cfg, adaptive_cfg, load_w_by,
    )
    return _apply_savings_floor(plan, prices, load_w_by, winter_cfg, adaptive_cfg)


def _winter_replay_prices(day: datetime) -> tuple[list[PriceSlot], dict[datetime, float]]:
    from ems.tests import test_replay as replay_fx

    price_rows = replay_fx._price_day(replay_fx._winter_price, day=day)
    slots = [
        PriceSlot(
            start=datetime.fromisoformat(p["start_ts"]),
            eur_per_kwh=p["eur_per_kwh"],
        )
        for p in price_rows
    ]
    load = {s.start: 1000.0 for s in slots}
    return slots, load


def test_must_charge_positive_empty_pool_shortfall():
    prices, load = _empty_pool_fixture()
    horizon = len(prices)
    off = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=horizon, daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=False),
    )
    on = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=horizon, daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(
            must_charge_enabled=True,
            must_charge_target_soc=30.0,
            must_charge_max_price_eur_per_kwh=0.40,
            must_charge_buffer_pp=10.0,
        ),
    )
    mc = [s for s in on.slots if is_must_charge_slot(s)]
    assert mc, "expected voorzorgslading charge slots"
    assert all(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in mc)
    assert all(MUST_CHARGE_REASON in (s.reason or "") for s in mc)
    assert all(s.target_soc == 30.0 for s in mc)
    assert all(s.deadline is not None for s in mc)
    assert all(s.start < s.deadline for s in mc)
    assert all(p.eur_per_kwh <= 0.40 for p in prices if p.start in {s.start for s in mc})
    assert not [s for s in off.slots if is_must_charge_slot(s)]
    evening_deadline = mc[0].deadline
    assert evening_deadline is not None
    assert evening_deadline > T0 + timedelta(hours=2)
    expensive_start = next(p.start for p in prices if p.eur_per_kwh >= 0.44)
    peak_at = expensive_start
    solar = {s.start: 0.0 for s in prices}
    slot_starts = [s.start for s in prices if s.start <= peak_at]
    price_by = {s.start: s.eur_per_kwh for s in prices}
    from ems.tests import test_replay as replay_fx

    cfg = replay_fx._cfg()
    soc_off = _final_soc_pct(
        slot_starts, load, solar, price_by, cfg=cfg, start_soc=12.0,
        intents={
            s.start: (
                off.intent_at(s.start).intent
                if off.intent_at(s.start)
                else BatteryIntent.ALLOW_SELF_CONSUMPTION
            )
            for s in prices
        },
        target_soc=off.target_soc,
    )
    soc_on = _final_soc_pct(
        slot_starts, load, solar, price_by, cfg=cfg, start_soc=12.0,
        intents={
            s.start: (
                on.intent_at(s.start).intent
                if on.intent_at(s.start)
                else BatteryIntent.ALLOW_SELF_CONSUMPTION
            )
            for s in prices
        },
        target_soc=on.target_soc,
    )
    assert soc_on > soc_off + 1.0


def test_must_charge_golden_disabled_byte_identical():
    from ems.tests import test_replay as replay_fx

    cases = [
        ("empty_pool", *_empty_pool_fixture(), T0, len(_empty_pool_fixture()[0])),
        (
            "winter_day",
            *_winter_replay_prices(replay_fx.DAY),
            replay_fx.DAY + timedelta(hours=6),
            96,
        ),
    ]
    for _label, prices, load, now, horizon in cases:
        winter_cfg = PlannerConfig(horizon_slots=horizon, daily_min_savings_eur=0.0)
        adaptive = _adaptive(must_charge_enabled=False)
        disabled = build_plan(
            "winter", prices=prices, forecast=[], now=now, soc_pct=12.0,
            winter_cfg=winter_cfg,
            summer_cfg=_SUMMER,
            load_w_by=load,
            adaptive_cfg=adaptive,
        )
        reference = _build_plan_without_must_charge(
            prices=prices, now=now, soc_pct=12.0,
            winter_cfg=winter_cfg, summer_cfg=_SUMMER, load_w_by=load,
            adaptive_cfg=adaptive,
        )
        assert _slot_tuple(disabled) == _slot_tuple(reference)
        assert disabled.target_soc == reference.target_soc
        assert disabled.deadline == reference.deadline


def test_guard_price_ceiling():
    prices, load = _empty_pool_fixture()
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=len(prices), daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(
            must_charge_enabled=True,
            must_charge_max_price_eur_per_kwh=0.30,
        ),
    )
    assert not [s for s in plan.slots if is_must_charge_slot(s)]


def test_guard_incomplete_prices():
    prices, load = _empty_pool_fixture()
    base = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=len(prices), daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=False),
    )
    cfg = MustChargeConfig(
        enabled=True, capability_ready=True, device_min_target_soc_pct=5.0,
        usable_kwh=10.8, effective_floor_soc_pct=10.0, max_charge_w=4000.0,
    )
    charged = apply_must_charge(base, prices, T0, soc_pct=12.0, cfg=cfg)
    mc_starts = {s.start for s in charged.slots if is_must_charge_slot(s)}
    assert mc_starts
    missing = next(iter(mc_starts))
    gapped = [p for p in prices if p.start != missing]
    out = apply_must_charge(base, gapped, T0, soc_pct=12.0, cfg=cfg)
    assert out is base


def test_guard_incomplete_prices_allows_sub_quarter_now():
    prices, load = _empty_pool_fixture()
    base = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=len(prices), daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=False),
    )
    cfg = MustChargeConfig(
        enabled=True, capability_ready=True, device_min_target_soc_pct=5.0,
        usable_kwh=10.8, effective_floor_soc_pct=10.0, max_charge_w=4000.0,
    )
    skewed = T0 + timedelta(microseconds=1)
    out = apply_must_charge(base, prices, skewed, soc_pct=12.0, cfg=cfg)
    assert [s for s in out.slots if is_must_charge_slot(s)]


def test_guard_unknown_capability():
    prices, load = _empty_pool_fixture()
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=len(prices), daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(
            must_charge_enabled=True,
            must_charge_capability_ready=False,
            must_charge_device_min_target_soc=None,
        ),
    )
    assert not [s for s in plan.slots if is_must_charge_slot(s)]


def test_guard_target_below_effective_floor():
    prices, load = _empty_pool_fixture()
    base = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=len(prices), daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=False),
    )
    cfg = MustChargeConfig(
        enabled=True,
        target_soc_pct=8.0,
        capability_ready=True,
        device_min_target_soc_pct=5.0,
        effective_floor_soc_pct=10.0,
        usable_kwh=10.8,
        max_charge_w=4000.0,
    )
    out = apply_must_charge(base, prices, T0, soc_pct=12.0, cfg=cfg)
    assert out is base
    assert not [s for s in out.slots if is_must_charge_slot(s)]


def test_savings_floor_does_not_strip_must_charge():
    slot = PlanSlot(
        T0, BatteryIntent.GRID_CHARGE_TO_TARGET, MUST_CHARGE_REASON,
        target_soc=30.0, power_w=4000.0, floor_soc=10.0, deadline=T0 + 8 * SLOT,
    )
    plan = Plan(created_at=T0, slots=(slot,), strategy="winter")
    prices = [PriceSlot(T0, 0.35)]
    out = apply_daily_savings_floor(plan, prices, None, 0.50, 0.90, 0.05, 0.02, 0.0)
    assert out.slots[0].intent is BatteryIntent.GRID_CHARGE_TO_TARGET


def test_validator_accepts_must_charge_plan():
    prices, load = _empty_pool_fixture()
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=len(prices), daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=True),
    )
    v = validate_plan(
        plan,
        soc_pct=12.0,
        data_quality="complete",
        min_reserve_soc=10.0,
        validate_projection=False,
    )
    assert v.status != "unsafe"


def test_dry_run_would_charge_without_writes():
    ctl = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0, target_soc=30.0)
    assert dec.outcome == "dry_run"
    assert dec.applied is False


def test_decision_reason_must_charge_summary():
    prices, load = _empty_pool_fixture()
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=len(prices), daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=True),
    )
    price_by = {p.start: p.eur_per_kwh for p in prices}
    reason = build_decision_reason(plan, price_by=price_by)
    assert reason.buy_pool is not None
    assert "Voorzorgslading" in reason.summary
    assert "0,40" in reason.summary
    assert reason.buy_pool.get("must_charge_summary")
    assert reason.buy_pool.get("must_charge_ceiling_eur_per_kwh") == 0.4


def test_replay_14_days_must_charge_on_vs_off():
    from ems.planner.budgets import plan_day_efc
    from ems.replay import _simulate
    from ems.tests import test_replay as replay_fx

    cfg = replay_fx._cfg()
    floor_buffer_pct = 10.0 + 10.0
    extra_kwh_on = 0.0
    extra_eur_on = 0.0
    max_daily_extra_kwh = 0.0
    max_daily_extra_eur = 0.0
    cap_exceed_days = 0
    cycle_exceed_days = 0
    extra_reserve_breaches = 0
    unprotected_off = 0
    unprotected_on = 0
    must_charge_days = 0

    for offset in range(14):
        now = T0 + timedelta(days=offset)
        prices, load = _empty_pool_fixture(now)
        winter_cfg = PlannerConfig(horizon_slots=len(prices), daily_min_savings_eur=0.0)
        ad_off = _adaptive(must_charge_enabled=False)
        ad_on = _adaptive(
            must_charge_enabled=True,
            must_charge_target_soc=30.0,
            must_charge_max_price_eur_per_kwh=0.40,
            must_charge_buffer_pp=10.0,
        )
        common = dict(
            prices=prices, forecast=[], now=now, soc_pct=12.0,
            summer_cfg=_SUMMER,
            load_w_by=load,
            winter_cfg=winter_cfg,
        )
        off = build_plan("winter", adaptive_cfg=ad_off, **common)
        on = build_plan("winter", adaptive_cfg=ad_on, **common)
        mc = [s for s in on.slots if is_must_charge_slot(s)]
        if mc:
            must_charge_days += 1
        price_by = {s.start: s.eur_per_kwh for s in prices}
        day_kwh = sum((s.power_w or 0) * 0.25 / 1000.0 for s in mc)
        day_eur = sum(
            (s.power_w or 0) * 0.25 / 1000.0 * price_by.get(s.start, 0.0) for s in mc
        )
        extra_kwh_on += day_kwh
        extra_eur_on += day_eur
        max_daily_extra_kwh = max(max_daily_extra_kwh, day_kwh)
        max_daily_extra_eur = max(max_daily_extra_eur, day_eur)
        cap = ad_on.max_daily_grid_charge_kwh
        if day_kwh > cap + 1e-6:
            cap_exceed_days += 1
        efc_by_day = plan_day_efc(
            on, ad_on.usable_kwh, round_trip_efficiency=ad_on.round_trip_efficiency,
            tz=ad_on.site_tz,
        )
        if any(v > ad_on.max_cycles_per_day + 1e-6 for v in efc_by_day.values()):
            cycle_exceed_days += 1
        peaks = [
            s.start for s in off.slots
            if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD and s.start > now
            and s.start.astimezone(ad_on.site_tz).hour in range(17, 22)
        ]
        if not peaks:
            continue
        peak_at = min(peaks)
        solar = {s.start: 0.0 for s in prices}
        slot_starts = [s.start for s in prices if s.start <= peak_at]

        def _soc_at_peak(
            plan: Plan,
            *,
            day_prices: list[PriceSlot],
            day_slot_starts: list[datetime],
            day_load: dict[datetime, float],
            day_solar: dict[datetime, float],
            day_price_by: dict[datetime, float],
        ) -> float:
            intents = {
                s.start: (
                    plan.intent_at(s.start).intent
                    if plan.intent_at(s.start)
                    else BatteryIntent.ALLOW_SELF_CONSUMPTION
                )
                for s in day_prices
            }
            return _final_soc_pct(
                day_slot_starts, day_load, day_solar, day_price_by, cfg=cfg,
                start_soc=12.0, intents=intents, target_soc=plan.target_soc,
            )

        peak_kw = dict(
            day_prices=prices, day_slot_starts=slot_starts, day_load=load,
            day_solar=solar, day_price_by=price_by,
        )
        if _soc_at_peak(off, **peak_kw) < floor_buffer_pct - 1e-6:
            unprotected_off += 1
        if _soc_at_peak(on, **peak_kw) < floor_buffer_pct - 1e-6:
            unprotected_on += 1
        sim_off = _simulate(
            [s.start for s in prices], load, solar, price_by, cfg=cfg,
            start_soc=12.0, battery_enabled=True,
            intents={
                s.start: (
                    off.intent_at(s.start).intent
                    if off.intent_at(s.start)
                    else BatteryIntent.ALLOW_SELF_CONSUMPTION
                )
                for s in prices
            },
            target_soc=off.target_soc,
        )
        sim_on = _simulate(
            [s.start for s in prices], load, solar, price_by, cfg=cfg,
            start_soc=12.0, battery_enabled=True,
            intents={
                s.start: (
                    on.intent_at(s.start).intent
                    if on.intent_at(s.start)
                    else BatteryIntent.ALLOW_SELF_CONSUMPTION
                )
                for s in prices
            },
            target_soc=on.target_soc,
        )
        extra_reserve_breaches += max(0, sim_on.reserve_breaches - sim_off.reserve_breaches)

    assert must_charge_days > 0
    assert unprotected_on < unprotected_off
    assert cap_exceed_days == 0
    assert cycle_exceed_days == 0
    assert extra_reserve_breaches == 0
    assert extra_kwh_on > 0.0
    assert max_daily_extra_kwh <= ad_on.max_daily_grid_charge_kwh + 1e-6


def test_settings_must_charge_defaults_and_bounds():
    from ems.settings import defaults, validate_settings

    d = defaults()
    assert d["planner.must_charge_enabled"] is False
    assert d["planner.must_charge_target_soc"] == 30.0
    clean, err = validate_settings({"planner.must_charge_target_soc": 1e6})
    assert "planner.must_charge_target_soc" in err
    clean, err = validate_settings({"planner.must_charge_max_price_eur_per_kwh": -1e6})
    assert "planner.must_charge_max_price_eur_per_kwh" in err
