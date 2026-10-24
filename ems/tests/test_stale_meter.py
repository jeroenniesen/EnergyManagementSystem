"""F-23 / #332: cautious planning when EV or grid meter freshness is uncertain."""
from __future__ import annotations

import dataclasses
import pickle
import re
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.base import _CONFIG_HASH_LEN, PlannerRequest, RuleBasedPlanner
from ems.planner.reason import (
    STALE_METER_PLAN_REASON,
    build_decision_reason,
    format_must_charge_slot_reason,
    is_must_charge_slot,
    stale_meter_reason_prefix,
)
from ems.planner.recovery import recover_if_needed
from ems.planner.rule_based import PlannerConfig
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.summer import SummerConfig
from ems.planner.validator import validate_plan
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


def _winter_fixture():
    prices = [PriceSlot(T0 + i * SLOT, e) for i, e in enumerate([0.08] * 4 + [0.45] * 4)]
    fc = [ForecastSlot(T0 + i * SLOT, 0, 0, 0) for i in range(8)]
    load = {T0 + i * SLOT: (200.0 if i < 4 else 3500.0) for i in range(8)}
    req = PlannerRequest(
        now=T0,
        prices=prices,
        forecast=fc,
        soc_pct=12.0,
        strategy="winter",
        winter_cfg=PlannerConfig(charge_slots=4, discharge_slots=4),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
        load_w_by=load,
        adaptive_cfg=AdaptiveConfig(
            usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
        ),
    )
    return req, prices


def _assert_grid_charge_lte_baseline(stale, baseline) -> None:
    assert _grid_charge_kwh(stale) <= _grid_charge_kwh(baseline) + 1e-9
    for s in stale.slots:
        if s.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
            continue
        match = next(b for b in baseline.slots if b.start == s.start)
        if match.intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            assert (s.target_kwh or 0.0) <= (match.target_kwh or 0.0) + 1e-9
            assert (s.power_w or 0.0) <= (match.power_w or 0.0) + 1e-9


def _field_has_default_value(field: dataclasses.Field, value: object) -> bool:
    if field.default is not dataclasses.MISSING:
        return value == field.default
    if field.default_factory is not dataclasses.MISSING:
        return value == field.default_factory()
    return False


def _assert_buy_pool_field_match(
    field: dataclasses.Field,
    actual,
    golden,
    pool_actual,
    pool_golden,
) -> bool:
    """Return True when a #324 buy-pool extension is accepted against the golden pickle."""
    if not _field_has_default_value(field, golden):
        return False
    if field.name == "auto_max_buy_eur_per_kwh":
        assert actual == pool_actual.max_buy_eur_per_kwh == pool_golden.max_buy_eur_per_kwh
        return True
    if field.name == "resident_cap_eur_per_kwh":
        assert actual in (None, 0, 0.0)
        return True
    return False


def _assert_value_matches_golden(actual: object, golden: object) -> None:
    if is_dataclass(golden) and not isinstance(golden, type):
        assert is_dataclass(actual) and type(actual) is type(golden)
        cls = type(golden)
        for field in fields(cls):
            if cls.__name__ == "PlannerInputSnapshot" and field.name == "config_hash":
                continue
            gv = getattr(golden, field.name)
            av = getattr(actual, field.name)
            if cls.__name__ == "BuyPoolInfo" and _assert_buy_pool_field_match(
                field, av, gv, actual, golden
            ):
                continue
            _assert_value_matches_golden(av, gv)
        return
    if isinstance(golden, tuple):
        assert isinstance(actual, tuple)
        assert len(actual) == len(golden)
        for a_item, g_item in zip(actual, golden, strict=True):
            _assert_value_matches_golden(a_item, g_item)
        return
    if isinstance(golden, list):
        assert isinstance(actual, list)
        assert len(actual) == len(golden)
        for a_item, g_item in zip(actual, golden, strict=True):
            _assert_value_matches_golden(a_item, g_item)
        return
    if isinstance(golden, dict):
        assert isinstance(actual, dict)
        assert actual.keys() == golden.keys()
        for key in golden:
            _assert_value_matches_golden(actual[key], golden[key])
        return
    assert actual == golden


def _assert_plan_matches_stale_meter_golden(plan: Plan, golden: Plan) -> None:
    _assert_value_matches_golden(plan, golden)
    assert plan.input_snapshot is not None
    ch = plan.input_snapshot.config_hash
    assert ch is not None
    assert re.fullmatch(rf"[0-9a-f]{{{_CONFIG_HASH_LEN}}}", ch)
    assert plan.buy_pool is not None
    assert golden.buy_pool is not None
    assert plan.buy_pool.resident_cap_eur_per_kwh in (None, 0, 0.0)
    assert (
        plan.buy_pool.auto_max_buy_eur_per_kwh
        == plan.buy_pool.max_buy_eur_per_kwh
        == golden.buy_pool.max_buy_eur_per_kwh
    )


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


def test_ev_stale_holds_allow_when_slot_would_cross_stale_floor():
    prices = [PriceSlot(T0 + i * SLOT, 0.10) for i in range(8)]
    fc = [ForecastSlot(T0 + i * SLOT, 0, 0, 0) for i in range(8)]
    load = {T0 + i * SLOT: 2500.0 for i in range(8)}
    req = PlannerRequest(
        now=T0,
        prices=prices,
        forecast=fc,
        soc_pct=18.0,
        strategy="summer",
        winter_cfg=PlannerConfig(),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
        load_w_by=load,
        adaptive_cfg=AdaptiveConfig(
            usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
        ),
        ev_stale=True,
    )
    baseline = RuleBasedPlanner().plan(replace(req, ev_stale=False))
    stale = RuleBasedPlanner().plan(req)
    assert stale.slots[0].intent is BatteryIntent.HOLD_RESERVE
    _assert_grid_charge_lte_baseline(stale, baseline)


def test_ev_stale_keeps_discharge_when_soc_stays_above_stale_floor():
    req, _, _ = _adaptive_fixture()
    stale = RuleBasedPlanner().plan(replace(req, soc_pct=80.0, ev_stale=True))
    peak_starts = {T0 + i * SLOT for i in range(4, 8)}
    for s in stale.slots:
        if s.start not in peak_starts:
            continue
        assert s.intent is BatteryIntent.DISCHARGE_FOR_LOAD
        assert STALE_METER_PLAN_REASON in (s.reason or "")


def test_ev_stale_holds_peak_that_drops_below_floor_after_charge_cap():
    """AC1: SoC walk respects target_soc; peak ending below 15% becomes HOLD_RESERVE."""
    req, _, _ = _adaptive_fixture()
    stale = RuleBasedPlanner().plan(replace(req, ev_stale=True))
    peak_starts = [T0 + i * SLOT for i in range(4, 8)]
    below_floor = peak_starts[3]
    above_floor = peak_starts[:3]
    for s in stale.slots:
        if s.start == below_floor:
            assert s.intent is BatteryIntent.HOLD_RESERVE
            assert STALE_METER_PLAN_REASON in (s.reason or "")
        elif s.start in above_floor:
            assert s.intent is BatteryIntent.DISCHARGE_FOR_LOAD
            assert STALE_METER_PLAN_REASON in (s.reason or "")


def test_ev_stale_raises_floor_and_trims_discharge():
    req, _, _ = _adaptive_fixture()
    baseline = RuleBasedPlanner().plan(req)
    stale_req = replace(req, ev_stale=True)
    stale = RuleBasedPlanner().plan(stale_req)
    assert any(STALE_METER_PLAN_REASON in (s.reason or "") for s in stale.slots)
    for s in stale.slots:
        if s.floor_soc is None:
            continue
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            base = next(b for b in baseline.slots if b.start == s.start)
            assert s.floor_soc == base.floor_soc
        else:
            assert s.floor_soc == 15.0
    base_discharge = {
        s.start for s in baseline.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD
    }
    stale_discharge = {
        s.start for s in stale.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD
    }
    assert stale_discharge <= base_discharge
    _assert_grid_charge_lte_baseline(stale, baseline)
    for s in stale.slots:
        if s.intent is BatteryIntent.HOLD_RESERVE and "meter data is uncertain" in (s.reason or ""):
            assert s.power_w is None


def test_plan_byte_identical_without_stale_golden():
    req, _, _ = _adaptive_fixture()
    plan = RuleBasedPlanner().plan(req)
    golden = pickle.loads(_GOLDEN.read_bytes())
    _assert_plan_matches_stale_meter_golden(plan, golden)


def test_plan_byte_identical_when_stale_signals_none():
    req, _, _ = _adaptive_fixture()
    req_none = replace(req, ev_stale=False, grid_stale=False)
    plan = RuleBasedPlanner().plan(req_none)
    golden = pickle.loads(_GOLDEN.read_bytes())
    _assert_plan_matches_stale_meter_golden(plan, golden)


def test_grid_stale_never_increases_grid_charge_vs_baseline():
    req, _ = _winter_fixture()
    baseline = RuleBasedPlanner().plan(req)
    for flags in ({"ev_stale": True}, {"grid_stale": True}):
        stale = RuleBasedPlanner().plan(replace(req, **flags))
        _assert_grid_charge_lte_baseline(stale, baseline)


def test_ev_stale_counterexample_stays_valid_grid_charge_floor_unchanged():
    """Review r3: low SoC + grid charge must not get raised floor_soc (unsafe plan)."""
    prices = [PriceSlot(T0 + i * SLOT, e) for i, e in enumerate([0.04] * 6 + [0.55] * 2)]
    fc = [ForecastSlot(T0 + i * SLOT, 0, 0, 0) for i in range(8)]
    load = {
        T0 + i * SLOT: (40.0 if i < 6 else 400.0) for i in range(8)
    }
    req = PlannerRequest(
        now=T0,
        prices=prices,
        forecast=fc,
        soc_pct=11.0,
        strategy="winter",
        winter_cfg=PlannerConfig(charge_slots=4, discharge_slots=2),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
        load_w_by=load,
        adaptive_cfg=AdaptiveConfig(
            usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
        ),
        ev_stale=True,
    )
    baseline = RuleBasedPlanner().plan(replace(req, ev_stale=False))
    stale = RuleBasedPlanner().plan(req)
    val = validate_plan(
        stale, soc_pct=11.0, min_reserve_soc=10.0, data_quality="complete",
    )
    assert val.ok
    assert any(
        s.intent is BatteryIntent.HOLD_RESERVE
        and "meter data is uncertain" in (s.reason or "")
        for s in stale.slots
    )
    for s in stale.slots:
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            base = next(b for b in baseline.slots if b.start == s.start)
            assert s.floor_soc == base.floor_soc
    _assert_grid_charge_lte_baseline(stale, baseline)


def test_stale_never_increases_grid_charge_after_recovery():
    req, prices = _winter_fixture()
    now = T0 + 3 * SLOT
    recover_kw = dict(
        soc_pct=12.0,
        prices=prices,
        usable_kwh=10.0,
        reserve_soc_pct=10.0,
        max_charge_w=4000.0,
        round_trip_efficiency=0.90,
    )
    baseline = RuleBasedPlanner().plan(req)
    base_after, _, _ = recover_if_needed(baseline, now, **recover_kw)
    for flags in ({"ev_stale": True}, {"grid_stale": True}):
        stale = RuleBasedPlanner().plan(replace(req, **flags))
        stale_after, _, _ = recover_if_needed(stale, now, **recover_kw)
        _assert_grid_charge_lte_baseline(stale_after, base_after)
    # Recovery catch-up slot keeps baseline reserve on floor_soc (#327); kWh/power already asserted.


def test_stale_must_charge_reason_prefix_still_counts_slots_and_summary():
    """#323 × #332: stale slot prefix must not hide must-charge from decision/validator."""
    from ems.tests.test_must_charge import _SUMMER, _adaptive, _empty_pool_fixture
    from ems.tests.test_must_charge import T0 as MUST_T0

    prices, load = _empty_pool_fixture()
    winter_cfg = PlannerConfig(horizon_slots=len(prices), daily_min_savings_eur=0.0)
    req = PlannerRequest(
        now=MUST_T0,
        prices=prices,
        forecast=[],
        soc_pct=12.0,
        strategy="winter",
        winter_cfg=winter_cfg,
        summer_cfg=_SUMMER,
        load_w_by=load,
        adaptive_cfg=_adaptive(must_charge_enabled=True),
    )
    planner = RuleBasedPlanner()
    baseline = planner.plan(replace(req, ev_stale=False))
    stale = planner.plan(replace(req, ev_stale=True))
    base_mc = [s for s in baseline.slots if is_must_charge_slot(s)]
    stale_mc = [s for s in stale.slots if is_must_charge_slot(s)]
    assert base_mc
    assert len(stale_mc) == len(base_mc)
    _assert_grid_charge_lte_baseline(stale, baseline)
    price_by = {p.start: p.eur_per_kwh for p in prices}
    reason = build_decision_reason(stale, price_by=price_by)
    assert STALE_METER_PLAN_REASON in reason.summary
    assert "Voorzorgslading" in reason.summary
    assert "Geen koopmoment" not in reason.summary
    assert reason.buy_pool is not None
    assert reason.buy_pool.get("must_charge_summary")
    assert reason.buy_pool.get("must_charge_ceiling_eur_per_kwh") == 0.4

    unsized = PlanSlot(
        MUST_T0,
        BatteryIntent.GRID_CHARGE_TO_TARGET,
        stale_meter_reason_prefix(format_must_charge_slot_reason(0.40)),
        power_w=4000.0,
    )
    assert is_must_charge_slot(unsized)
    v = validate_plan(
        Plan(created_at=MUST_T0, slots=(unsized,), strategy="winter"),
        soc_pct=12.0,
        data_quality="complete",
        min_reserve_soc=10.0,
        validate_projection=False,
    )
    assert v.status == "unsafe"
    assert any(f.code == "must_charge_unsized" for f in v.findings)
