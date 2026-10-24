"""#323 optional must-charge before the evening peak (audit F-02)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ems.control.mode_controller import Lifecycle, ModeController
from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.economics import apply_daily_savings_floor
from ems.planner.must_charge import MustChargeConfig, apply_must_charge
from ems.planner.reason import MUST_CHARGE_REASON, build_decision_reason, is_must_charge_slot
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.strategy import build_plan
from ems.planner.summer import SummerConfig
from ems.planner.validator import validate_plan
from ems.sources.battery import MockBatteryDriver
from ems.sources.prices import PriceSlot

T0 = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
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


def _empty_pool_fixture():
    prices = (
        [PriceSlot(T0 + i * SLOT, 0.37) for i in range(32)]
        + [PriceSlot(T0 + 32 * SLOT + i * SLOT, 0.45) for i in range(8)]
        + [PriceSlot(T0 + 40 * SLOT + i * SLOT, 0.05) for i in range(12)]
    )
    load = {p.start: (3500.0 if p.eur_per_kwh >= 0.44 else 500.0) for p in prices}
    return prices, load


def _control_signature(plan: Plan):
    return [(s.intent, s.target_soc, s.target_kwh, s.power_w) for s in plan.slots]


def test_must_charge_positive_empty_pool_shortfall():
    prices, load = _empty_pool_fixture()
    off = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=52, daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=False),
    )
    on = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=52, daily_min_savings_eur=0.0),
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


def test_must_charge_golden_disabled_byte_identical():
    prices, load = _empty_pool_fixture()
    a = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=52),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=False),
    )
    b = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=52),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(),
    )
    assert repr(_control_signature(a)) == repr(_control_signature(b))


def test_guard_price_ceiling():
    prices, load = _empty_pool_fixture()
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=52, daily_min_savings_eur=0.0),
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
    sparse = prices[:40]
    plan = plan_rule_based(
        sparse, T0, PlannerConfig(horizon_slots=40), soc_pct=12.0, load_w_by=load,
        usable_kwh=10.8, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    cfg = MustChargeConfig(
        enabled=True, capability_ready=True, device_min_target_soc_pct=5.0,
        usable_kwh=10.8, effective_floor_soc_pct=10.0, max_charge_w=4000.0,
    )
    out = apply_must_charge(plan, sparse, T0, soc_pct=12.0, cfg=cfg)
    assert out is plan


def test_guard_unknown_capability():
    prices, load = _empty_pool_fixture()
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=12.0,
        winter_cfg=PlannerConfig(horizon_slots=52, daily_min_savings_eur=0.0),
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
    cfg = MustChargeConfig(
        enabled=True,
        target_soc_pct=8.0,
        capability_ready=True,
        device_min_target_soc_pct=5.0,
        effective_floor_soc_pct=10.0,
    )
    assert apply_must_charge(
        Plan(created_at=T0, slots=()), [], T0, soc_pct=5.0, cfg=cfg,
    ).slots == ()


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
        winter_cfg=PlannerConfig(horizon_slots=52, daily_min_savings_eur=0.0),
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
        winter_cfg=PlannerConfig(horizon_slots=52, daily_min_savings_eur=0.0),
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=True),
    )
    price_by = {p.start: p.eur_per_kwh for p in prices}
    reason = build_decision_reason(plan, price_by=price_by)
    assert reason.buy_pool is not None
    assert "Voorzorgslading" in reason.summary
    assert "plafond" in reason.summary.lower()
    assert reason.buy_pool.get("must_charge_summary")


def test_replay_14_days_must_charge_on_vs_off():
    from dataclasses import replace

    from ems.replay import _adaptive_cfg, _simulate, _winter_cfg
    from ems.tests import test_replay as replay_fx

    cfg = replay_fx._cfg()
    extra_kwh_on = 0.0
    extra_eur_on = 0.0
    max_daily_extra_kwh = 0.0
    cap_exceed_days = 0
    extra_reserve_breaches = 0

    for offset in range(14):
        day = replay_fx.DAY + timedelta(days=offset)
        price_rows = replay_fx._price_day(replay_fx._winter_price, day=day)
        slots = [
            PriceSlot(
                start=datetime.fromisoformat(p["start_ts"]),
                eur_per_kwh=p["eur_per_kwh"],
            )
            for p in price_rows
        ]
        load = {s.start: 1000.0 for s in slots}
        winter = _winter_cfg(cfg)
        ad_off = _adaptive_cfg(cfg)
        ad_on = replace(
            ad_off,
            must_charge_enabled=True,
            must_charge_capability_ready=True,
            must_charge_device_min_target_soc=5.0,
            must_charge_target_soc=30.0,
            must_charge_max_price_eur_per_kwh=0.40,
            must_charge_buffer_pp=10.0,
        )
        common = dict(
            prices=slots, forecast=[], now=day, soc_pct=8.0,
            summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
            load_w_by=load,
            winter_cfg=replace(winter, daily_min_savings_eur=0.0),
        )
        off = build_plan("winter", adaptive_cfg=ad_off, **common)
        on = build_plan("winter", adaptive_cfg=ad_on, **common)
        mc = [s for s in on.slots if is_must_charge_slot(s)]
        price_by = {s.start: s.eur_per_kwh for s in slots}
        day_kwh = sum((s.power_w or 0) * 0.25 / 1000.0 for s in mc)
        day_eur = sum(
            (s.power_w or 0) * 0.25 / 1000.0 * price_by.get(s.start, 0.0) for s in mc
        )
        extra_kwh_on += day_kwh
        extra_eur_on += day_eur
        max_daily_extra_kwh = max(max_daily_extra_kwh, day_kwh)
        cap = ad_on.max_daily_grid_charge_kwh
        if day_kwh > cap + 1e-6:
            cap_exceed_days += 1
        solar = {s.start: 0.0 for s in slots}
        day_slots = list(slots)

        def intents(plan: Plan, starts=day_slots):
            return {
                s.start: (
                    plan.intent_at(s.start).intent
                    if plan.intent_at(s.start)
                    else BatteryIntent.ALLOW_SELF_CONSUMPTION
                )
                for s in starts
            }

        sim_off = _simulate(
            [s.start for s in slots], load, solar, price_by, cfg=cfg,
            start_soc=8.0, battery_enabled=True, intents=intents(off),
            target_soc=off.target_soc,
        )
        sim_on = _simulate(
            [s.start for s in slots], load, solar, price_by, cfg=cfg,
            start_soc=8.0, battery_enabled=True, intents=intents(on),
            target_soc=on.target_soc,
        )
        extra_reserve_breaches += max(0, sim_on.reserve_breaches - sim_off.reserve_breaches)

    assert cap_exceed_days == 0
    assert extra_reserve_breaches == 0
    assert extra_kwh_on >= 0.0


def test_settings_must_charge_defaults_and_bounds():
    from ems.settings import defaults, validate_settings

    d = defaults()
    assert d["planner.must_charge_enabled"] is False
    assert d["planner.must_charge_target_soc"] == 30.0
    clean, err = validate_settings({"planner.must_charge_target_soc": 1e6})
    assert "planner.must_charge_target_soc" in err
    clean, err = validate_settings({"planner.must_charge_max_price_eur_per_kwh": -1e6})
    assert "planner.must_charge_max_price_eur_per_kwh" in err
