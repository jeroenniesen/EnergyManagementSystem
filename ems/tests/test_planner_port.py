"""Planner port + registry + PlannerInputSnapshot (BACKLOG B-47).

Canned prices/forecast only — no hardware, no network. Validator behaviour must stay fail-safe.
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent, PlannerInputSnapshot, PlannerMode
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.base import (
    PlannerRequest,
    RuleBasedPlanner,
    config_hash_for_request,
    digest_forecast,
    digest_load,
    digest_prices,
)
from ems.planner.factory import (
    build_planner,
    registered_planners,
)
from ems.planner.rule_based import PlannerConfig
from ems.planner.schedule import Plan, PlanSlot
from ems.planner.strategy import build_plan
from ems.planner.summer import SummerConfig
from ems.planner.validator import validate_plan
from ems.ports import (
    BatteryDriver,
    Planner,
    PriceSource,
    SolarForecastSource,
    Source,
)
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")
T0 = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)


def _prices(n: int = 32) -> list[PriceSlot]:
    # Cheap night, expensive morning peak — winter arbitrage finds a spread.
    return [
        PriceSlot(T0 + i * SLOT, 0.05 if i < 12 else (0.40 if i < 20 else 0.12))
        for i in range(n)
    ]


def _forecast(n: int = 16) -> list[ForecastSlot]:
    return [
        ForecastSlot(T0 + i * SLOT, p10_w=100.0, p50_w=200.0, p90_w=300.0)
        for i in range(n)
    ]


def _request(**overrides) -> PlannerRequest:
    base = dict(
        now=T0,
        prices=_prices(),
        forecast=_forecast(),
        soc_pct=40.0,
        strategy="winter",
        winter_cfg=PlannerConfig(charge_slots=4, discharge_slots=4),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=70.0),
        planner_mode=PlannerMode.RULE_BASED,
        price_provenance="MockPriceSource",
        forecast_provider="mock",
        baseline="load_profile",
    )
    base.update(overrides)
    return PlannerRequest(**base)


def test_registry_includes_rule_based_only():
    assert registered_planners() == ("rule_based",)


def test_build_planner_rule_based_conforms_to_port():
    planner = build_planner("rule_based")
    assert isinstance(planner, RuleBasedPlanner)
    assert isinstance(planner, Planner)


def test_ml_and_advisory_fall_back_to_rule_based():
    for mode in ("ml", "advisory", PlannerMode.ML, "not_a_mode", None, ""):
        planner = build_planner(mode)
        assert isinstance(planner, RuleBasedPlanner)


def test_ports_catalog_reexports_source_protocols():
    assert Source is not None
    assert BatteryDriver is not None
    assert PriceSource is not None
    assert SolarForecastSource is not None
    assert Planner is not None


def test_adapter_matches_direct_build_plan_slots():
    req = _request()
    via_port = RuleBasedPlanner().plan(req)
    direct = build_plan(
        req.strategy,
        prices=req.prices,
        forecast=req.forecast,
        now=req.now,
        soc_pct=req.soc_pct,
        winter_cfg=req.winter_cfg,
        summer_cfg=req.summer_cfg,
    )
    assert via_port.strategy == direct.strategy == "winter"
    assert via_port.target_soc == direct.target_soc
    assert via_port.deadline == direct.deadline
    assert len(via_port.slots) == len(direct.slots)
    assert [s.intent for s in via_port.slots] == [s.intent for s in direct.slots]


def test_plan_carries_versioned_input_snapshot():
    plan = RuleBasedPlanner().plan(_request(soc_pct=55.0, strategy="winter"))
    assert plan.version == 1
    assert plan.id and len(plan.id) == 16
    assert plan.planner_mode == "rule_based"
    snap = plan.input_snapshot
    assert isinstance(snap, PlannerInputSnapshot)
    assert snap.taken_at == T0
    assert snap.planner_mode == "rule_based"
    assert snap.strategy == "winter"
    assert snap.soc_pct == 55.0
    assert snap.price_slots == 32
    assert snap.price_resolution_minutes == 15
    assert snap.price_provenance == "MockPriceSource"
    assert snap.price_min_eur == 0.05
    assert snap.price_max_eur == 0.40
    assert snap.prices_digest == digest_prices(_prices())
    assert snap.forecast_slots == 16
    assert snap.forecast_provider == "mock"
    assert snap.forecast_p50_kwh is not None and snap.forecast_p50_kwh > 0
    assert snap.forecast_digest == digest_forecast(_forecast())
    assert snap.load_digest is None  # no load_w_by on this request
    assert snap.baseline == "load_profile"
    assert snap.config_hash == config_hash_for_request(
        _request(soc_pct=55.0, strategy="winter"), strategy="winter",
    )
    assert snap.config_hash and len(snap.config_hash) == 12


def test_summer_strategy_via_port():
    now = datetime(2026, 6, 28, 8, 0, tzinfo=UTC)
    prices = [PriceSlot(now + i * SLOT, 0.20) for i in range(24)]
    forecast = [
        ForecastSlot(now + i * SLOT, p10_w=500.0, p50_w=800.0, p90_w=1000.0)
        for i in range(24)
    ]
    plan = build_planner("rule_based").plan(_request(
        now=now, prices=prices, forecast=forecast, strategy="summer", soc_pct=60.0,
    ))
    assert plan.strategy == "summer"
    assert plan.input_snapshot is not None
    assert plan.input_snapshot.strategy == "summer"


def test_validator_still_rejects_unsafe_data_quality():
    plan = RuleBasedPlanner().plan(_request())
    # Force a charge slot so stale inputs are control-blocking when quality is unsafe.
    if not any(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in plan.slots):
        plan = Plan(
            created_at=T0,
            slots=(PlanSlot(
                T0, BatteryIntent.GRID_CHARGE_TO_TARGET, "test",
                target_soc=80.0, floor_soc=10.0, deadline=T0 + timedelta(hours=4),
            ),),
            strategy="winter",
            target_soc=80.0,
            deadline=T0 + timedelta(hours=4),
            input_snapshot=plan.input_snapshot,
            planner_mode="rule_based",
            id=plan.id,
            version=1,
        )
    v = validate_plan(
        plan, soc_pct=40.0, data_quality="unsafe", min_reserve_soc=10.0,
    )
    assert v.ok is False
    assert v.status == "unsafe"


def test_validator_still_rejects_target_out_of_range():
    plan = Plan(
        created_at=T0,
        slots=(PlanSlot(
            T0, BatteryIntent.GRID_CHARGE_TO_TARGET, "bad",
            target_soc=130.0, floor_soc=10.0,
        ),),
        strategy="winter",
    )
    v = validate_plan(plan, soc_pct=40.0, data_quality="complete", min_reserve_soc=10.0)
    assert v.ok is False
    assert any(f.code == "target_out_of_range" for f in v.findings)


def test_config_hash_changes_with_import_fee_and_night_reserve_target():
    """config_hash must reflect adapter configs — import fee and summer target.

    Night-reserve-driven summer target_soc is folded into SummerConfig by the control builders;
    hashing asdict(summer_cfg) covers that without a hand-maintained settings key list.
    """
    baseline = _request(
        winter_cfg=PlannerConfig(charge_slots=4, discharge_slots=4, import_fee_eur_per_kwh=0.0),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=70.0),
    )
    fee_changed = _request(
        winter_cfg=PlannerConfig(charge_slots=4, discharge_slots=4, import_fee_eur_per_kwh=0.05),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=70.0),
    )
    target_changed = _request(
        winter_cfg=PlannerConfig(charge_slots=4, discharge_slots=4, import_fee_eur_per_kwh=0.0),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=85.0),  # night_reserve raised
    )
    h0 = config_hash_for_request(baseline, strategy="winter")
    h_fee = config_hash_for_request(fee_changed, strategy="winter")
    h_target = config_hash_for_request(target_changed, strategy="winter")
    assert h0 == config_hash_for_request(baseline, strategy="winter")  # stable
    assert h0 != h_fee
    assert h0 != h_target
    assert h_fee != h_target
    # Via the port: snapshot picks up the same fingerprint.
    snap_fee = RuleBasedPlanner().plan(fee_changed).input_snapshot
    assert snap_fee is not None
    assert snap_fee.config_hash == h_fee


def test_adaptive_winter_via_port_attaches_snapshot():
    load = {T0 + i * SLOT: 800.0 for i in range(32)}
    cfg = AdaptiveConfig(
        usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    plan = RuleBasedPlanner().plan(_request(
        load_w_by=load, adaptive_cfg=cfg, strategy="winter", soc_pct=35.0,
    ))
    assert plan.input_snapshot is not None
    assert plan.strategy == "winter"
    assert isinstance(plan, Plan)
    assert plan.input_snapshot.load_digest == digest_load(load)


def test_series_digests_are_stable_and_detect_drift():
    prices = _prices()
    forecast = _forecast()
    load = {T0 + i * SLOT: 500.0 + i for i in range(8)}
    assert digest_prices(prices) == digest_prices(list(prices))
    assert digest_forecast(forecast) == digest_forecast(list(forecast))
    assert digest_load(load) == digest_load(dict(load))
    # Order-independent for load (sorted by start).
    scrambled = {T0 + 7 * SLOT: 507.0, **{T0 + i * SLOT: 500.0 + i for i in range(7)}}
    assert digest_load(load) == digest_load(scrambled)
    # Drift: one price tick changes the digest.
    drifted = list(prices)
    drifted[0] = PriceSlot(drifted[0].start, drifted[0].eur_per_kwh + 0.01)
    assert digest_prices(prices) != digest_prices(drifted)
    # Empty → None (nothing to fingerprint).
    assert digest_prices([]) is None
    assert digest_forecast([]) is None
    assert digest_load(None) is None
    assert digest_load({}) is None


def test_snapshot_does_not_persist_full_slot_series():
    load = {T0 + i * SLOT: 800.0 for i in range(8)}
    plan = RuleBasedPlanner().plan(_request(load_w_by=load))
    snap = plan.input_snapshot
    assert snap is not None
    field_names = {f.name for f in dataclasses.fields(snap)}
    # Compact+digest surface — never store the live series on the snapshot.
    assert "prices" not in field_names
    assert "forecast" not in field_names
    assert "load_w_by" not in field_names
    assert "slots" not in field_names
    values = dataclasses.astuple(snap)
    assert not any(isinstance(v, (list, tuple, dict)) for v in values)
    assert snap.prices_digest and snap.forecast_digest and snap.load_digest
    assert len(snap.prices_digest) == 16
    assert len(snap.forecast_digest) == 16
    assert len(snap.load_digest) == 16
