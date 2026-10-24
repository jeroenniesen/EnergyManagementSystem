"""Resident max purchase price cap (#324 / audit F-15)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.economics import apply_max_buy_cap, max_buy_for_peak
from ems.planner.reason import build_decision_reason
from ems.planner.recovery import build_catch_up_plan
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import SLOT
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot
from ems.tests.test_plan_memoization import _app

AMS = ZoneInfo("Europe/Amsterdam")
T0 = datetime(2026, 1, 15, 6, 0, tzinfo=AMS)
WINTER_DAY = datetime(2026, 10, 16, 6, 0, tzinfo=AMS)


def _late_pack_winter_fixture():
    """Valley + evening peak; uncapped plan buys slots above €0,20."""
    day = datetime(2026, 10, 5, 8, 0, tzinfo=AMS)

    def block(hour: int, n: int, price: float) -> list[PriceSlot]:
        start = datetime(2026, 10, 5, hour, 0, tzinfo=AMS)
        return [PriceSlot(start + i * SLOT, price) for i in range(n)]

    prices = (
        block(8, 16, 0.32)
        + block(12, 4, 0.228)
        + block(13, 4, 0.172)
        + block(14, 4, 0.182)
        + block(15, 8, 0.239)
        + block(17, 8, 0.30)
        + block(19, 8, 0.449)
    )
    load = {p.start: (4500.0 if p.eur_per_kwh >= 0.40 else 400.0) for p in prices}
    cfg = PlannerConfig(charge_slots=12, discharge_slots=24, daily_min_savings_eur=0.0)
    return prices, day, load, cfg


def _winter_cfg(**kw) -> PlannerConfig:
    base = dict(
        round_trip_efficiency=0.90,
        degradation_eur_per_kwh=0.05,
        risk_margin_eur_per_kwh=0.02,
        charge_slots=12,
        discharge_slots=24,
        horizon_slots=20,
    )
    base.update(kw)
    return PlannerConfig(**base)


def _adaptive_cfg(**kw) -> AdaptiveConfig:
    base = dict(
        usable_kwh=10.8,
        reserve_soc_pct=10.0,
        round_trip_efficiency=0.90,
        max_charge_w=4000.0,
        degradation_eur_per_kwh=0.05,
        risk_margin_eur_per_kwh=0.02,
        horizon_slots=12,
    )
    base.update(kw)
    return AdaptiveConfig(**base)


def test_apply_max_buy_cap_zero_leaves_auto():
    assert apply_max_buy_cap(0.297, 0.0) == 0.297
    assert apply_max_buy_cap(float("inf"), 0.0) == float("inf")


def test_apply_max_buy_cap_lowers_when_set():
    assert apply_max_buy_cap(0.297, 0.20) == 0.20
    assert apply_max_buy_cap(float("inf"), 0.20) == 0.20


def test_winter_cap_020_no_grid_charge_above_cap():
    prices, day, load, cfg = _late_pack_winter_fixture()
    price_by = {p.start: p.eur_per_kwh for p in prices}
    uncapped = plan_rule_based(
        prices, day, cfg, soc_pct=8.0, load_w_by=load, usable_kwh=10.8,
        reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    capped = plan_rule_based(
        prices, day, replace(cfg, max_buy_eur_per_kwh=0.20),
        soc_pct=8.0, load_w_by=load, usable_kwh=10.8, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    uncapped_px = {
        price_by[s.start] for s in uncapped.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    }
    assert uncapped_px and any(px > 0.20 for px in uncapped_px)
    for s in capped.slots:
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            assert price_by[s.start] <= 0.20


def test_winter_cap_zero_matches_unset():
    prices, day, load, cfg = _late_pack_winter_fixture()
    unset = plan_rule_based(
        prices, day, cfg, soc_pct=8.0, load_w_by=load, usable_kwh=10.8,
        reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    explicit_zero = plan_rule_based(
        prices, day, replace(cfg, max_buy_eur_per_kwh=0.0),
        soc_pct=8.0, load_w_by=load, usable_kwh=10.8, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    assert [(s.start, s.intent) for s in unset.slots] == [
        (s.start, s.intent) for s in explicit_zero.slots
    ]


def test_adaptive_cap_020_no_grid_charge_above_cap():
    prices, day, load, _cfg = _late_pack_winter_fixture()
    fc = [ForecastSlot(p.start, 0.0, 0.0, 0.0) for p in prices]
    plan = plan_adaptive(
        prices, fc, day, soc_pct=8.0, load_w_by=load,
        cfg=_adaptive_cfg(max_buy_eur_per_kwh=0.20),
    )
    price_by = {p.start: p.eur_per_kwh for p in prices}
    for s in plan.slots:
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            assert price_by[s.start] <= 0.20


def test_adaptive_cap_zero_regression():
    prices, day, load, _cfg = _late_pack_winter_fixture()
    fc = [ForecastSlot(p.start, 0.0, 0.0, 0.0) for p in prices]
    a = plan_adaptive(
        prices, fc, day, soc_pct=8.0, load_w_by=load,
        cfg=_adaptive_cfg(),
    )
    b = plan_adaptive(
        prices, fc, day, soc_pct=8.0, load_w_by=load,
        cfg=_adaptive_cfg(max_buy_eur_per_kwh=0.0),
    )
    assert [(s.start, s.intent) for s in a.slots] == [(s.start, s.intent) for s in b.slots]


def test_recovery_cap_020_without_discharge_prices():
    """Cap still applies when there is no discharge peak (max_buy would be inf)."""
    from ems.planner.schedule import Plan, PlanSlot

    plan = Plan(
        created_at=T0, strategy="winter", target_soc=80.0, deadline=T0 + 8 * SLOT,
        slots=tuple(
            PlanSlot(T0 + i * SLOT, BatteryIntent.ALLOW_SELF_CONSUMPTION, "x") for i in range(8)
        ),
    )
    prices = [PriceSlot(T0 + i * SLOT, 0.25 if i < 4 else 0.50) for i in range(8)]
    caught = build_catch_up_plan(
        plan, T0, soc_pct=30.0, prices=prices, usable_kwh=10.0, reserve_soc_pct=10.0,
        max_charge_w=4000.0, round_trip_efficiency=0.90, max_buy_eur_per_kwh=0.20,
    )
    charge_px = {
        prices[int((s.start - T0) / SLOT)].eur_per_kwh
        for s in caught.plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    }
    assert all(px <= 0.20 for px in charge_px)


def test_recovery_cap_zero_regression():
    from ems.planner.schedule import Plan, PlanSlot

    plan = Plan(
        created_at=T0, strategy="winter", target_soc=80.0, deadline=T0 + 10 * SLOT,
        slots=tuple(
            PlanSlot(T0 + 8 * SLOT, BatteryIntent.DISCHARGE_FOR_LOAD, "peak")
            if i == 8
            else PlanSlot(T0 + i * SLOT, BatteryIntent.ALLOW_SELF_CONSUMPTION, "x")
            for i in range(10)
        ),
    )
    prices = [
        PriceSlot(T0 + i * SLOT, 0.40 if i == 8 else (0.30 if i == 2 else 0.90))
        for i in range(10)
    ]
    a = build_catch_up_plan(
        plan, T0, soc_pct=70.0, prices=prices, usable_kwh=10.0, reserve_soc_pct=10.0,
        max_charge_w=4000.0, round_trip_efficiency=0.85,
        degradation_eur_per_kwh=0.03, risk_margin_eur_per_kwh=0.01,
    )
    b = build_catch_up_plan(
        plan, T0, soc_pct=70.0, prices=prices, usable_kwh=10.0, reserve_soc_pct=10.0,
        max_charge_w=4000.0, round_trip_efficiency=0.85,
        degradation_eur_per_kwh=0.03, risk_margin_eur_per_kwh=0.01,
        max_buy_eur_per_kwh=0.0,
    )
    a_slots = [(s.start, s.intent) for s in a.plan.slots]
    b_slots = [(s.start, s.intent) for s in b.plan.slots]
    assert a_slots == b_slots


def test_empty_buy_pool_mentions_resident_limit():
    """AC2: jouw limiet €0,20 when the resident cap empties the pool."""
    t0 = WINTER_DAY
    prices = (
        [PriceSlot(t0 + i * SLOT, 0.37) for i in range(32)]
        + [PriceSlot(t0 + 32 * SLOT + i * SLOT, 0.45) for i in range(8)]
        + [PriceSlot(t0 + 40 * SLOT + i * SLOT, 0.05) for i in range(12)]
    )
    load = {p.start: (3500.0 if p.eur_per_kwh >= 0.44 else 500.0) for p in prices}
    plan = plan_rule_based(
        prices, t0,
        PlannerConfig(horizon_slots=52, max_buy_eur_per_kwh=0.20),
        soc_pct=5.5, load_w_by=load, usable_kwh=10.8, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    assert plan.buy_pool is not None
    assert plan.buy_pool.state == "empty"
    assert plan.buy_pool.shortfall_kwh > 0
    auto = max_buy_for_peak(0.37, rte=0.90, deg=0.05, risk=0.02)
    assert auto > 0.20
    summary = build_decision_reason(plan, price_by={p.start: p.eur_per_kwh for p in prices}).summary
    assert "jouw limiet €0,20" in summary


def test_max_buy_hot_apply_replans(tmp_path):
    """AC4: POST /api/settings cap change must not serve a stale plan."""
    app = _app(tmp_path)
    svc = app.state.control_service
    with TestClient(app) as client:
        client.post("/api/settings", json={"strategy.mode": "winter"})
        client.get("/api/battery-plan")
        builds_before = svc.plan_builds
        plan1 = client.get("/api/plan").json()
        mb1 = plan1["buy_pool"]["max_buy_eur_per_kwh"]
        r = client.post("/api/settings", json={"planner.max_buy_eur_per_kwh": 0.15})
        assert r.status_code == 200
        plan2 = client.get("/api/plan").json()
        mb2 = plan2["buy_pool"]["max_buy_eur_per_kwh"]
        assert mb2 <= mb1
        assert svc.plan_builds == builds_before + 1
