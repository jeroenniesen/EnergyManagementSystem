"""F-23 / #332: cautious planning when EV or grid meter freshness is uncertain."""
from __future__ import annotations

import pickle
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.base import PlannerRequest, RuleBasedPlanner
from ems.planner.reason import STALE_METER_PLAN_REASON, apply_stale_meter_caution
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import SLOT
from ems.planner.summer import SummerConfig
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
_GOLDEN = Path(__file__).parent / "fixtures" / "stale_meter_golden_plan.pkl"


def _adaptive_fixture():
    prices = [PriceSlot(T0 + i * SLOT, 0.10 if i < 4 else 0.40) for i in range(8)]
    fc = [ForecastSlot(T0 + i * SLOT, 0, 0, 0) for i in range(8)]
    load = {T0 + i * SLOT: (200.0 if i < 4 else 3000.0) for i in range(8)}
    req = PlannerRequest(
        now=T0,
        prices=prices,
        forecast=fc,
        soc_pct=15.0,
        strategy="summer",
        winter_cfg=PlannerConfig(),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
        load_w_by=load,
        adaptive_cfg=AdaptiveConfig(
            usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
        ),
    )
    return req, prices, load


def _grid_charge_kwh(plan) -> float:
    total = 0.0
    for s in plan.slots:
        if s.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
            continue
        if s.target_kwh is not None:
            total += float(s.target_kwh)
        elif s.power_w is not None:
            total += float(s.power_w) * 0.25 / 1000.0
    return total


def test_ev_stale_raises_floor_and_trims_discharge():
    req, _, _ = _adaptive_fixture()
    baseline = RuleBasedPlanner().plan(req)
    stale_req = replace(req, ev_stale=True)
    stale = RuleBasedPlanner().plan(stale_req)
    assert any(STALE_METER_PLAN_REASON in (s.reason or "") for s in stale.slots)
    assert all(
        (s.floor_soc or 0.0) >= 15.0 - 1e-9 for s in stale.slots if s.floor_soc is not None
    )
    base_discharge = {
        s.start for s in baseline.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD
    }
    stale_discharge = {
        s.start for s in stale.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD
    }
    assert stale_discharge <= base_discharge
    assert _grid_charge_kwh(stale) <= _grid_charge_kwh(baseline) + 1e-9


def test_plan_byte_identical_without_stale_golden():
    req, _, _ = _adaptive_fixture()
    plan = RuleBasedPlanner().plan(req)
    golden = pickle.loads(_GOLDEN.read_bytes())
    assert pickle.dumps(plan) == pickle.dumps(golden)


def test_plan_byte_identical_when_stale_signals_none():
    req, _, _ = _adaptive_fixture()
    req_none = replace(req, ev_stale=False, grid_stale=False)
    assert pickle.dumps(RuleBasedPlanner().plan(req_none)) == _GOLDEN.read_bytes()


def test_grid_stale_never_increases_grid_charge_vs_baseline():
    prices = [PriceSlot(T0 + i * SLOT, e) for i, e in enumerate([0.08] * 4 + [0.45] * 4)]
    load = {T0 + i * SLOT: (200.0 if i < 4 else 3500.0) for i in range(8)}
    cfg = PlannerConfig(charge_slots=4, discharge_slots=4)
    baseline = plan_rule_based(
        prices, T0, cfg, soc_pct=12.0, load_w_by=load, usable_kwh=10.0,
        reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    stale = apply_stale_meter_caution(
        baseline, base_reserve_soc_pct=10.0, buffer_pp=5.0,
    )
    assert _grid_charge_kwh(stale) <= _grid_charge_kwh(baseline) + 1e-9
    for s in stale.slots:
        if s.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
            continue
        match = next(b for b in baseline.slots if b.start == s.start)
        if match.intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            assert (s.target_kwh or 0.0) <= (match.target_kwh or 0.0) + 1e-9
            assert (s.power_w or 0.0) <= (match.power_w or 0.0) + 1e-9
