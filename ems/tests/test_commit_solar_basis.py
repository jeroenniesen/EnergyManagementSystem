"""#330 / F-18: size committed grid charge on a real Solcast P10, never above today's P50 path."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.application.context import ApplicationContext
from ems.application.services.plan import PlanService
from ems.clock import FrozenClock
from ems.control.service import ControlContext, ControlService
from ems.domain import BatteryIntent
from ems.planner.adaptive import (
    AdaptiveConfig,
    commitment_solar_basis,
    committed_solar_w,
    plan_adaptive,
)
from ems.planner.projection import ProjectedSlot, mark_floor_holds
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.summer import SummerConfig, plan_summer
from ems.planner.validator import PlanValidation
from ems.replay import ReplayConfig, _adaptive_cfg, _summer_cfg
from ems.settings import SETTINGS_BY_KEY, defaults, validate_settings
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
AMS = ZoneInfo("Europe/Amsterdam")


def _bought(plan) -> float:
    return sum(
        float(s.target_kwh or 0.0)
        for s in plan.slots
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    )


def _contract(plan):
    return [
        (s.intent, s.reason, s.target_soc, s.target_kwh, s.power_w, s.floor_soc, s.deadline)
        for s in plan.slots
    ]


def _adaptive_prices():
    return [PriceSlot(T0 + i * SLOT, 0.10 if i < 4 else 0.40) for i in range(8)]


def _adaptive_load():
    return {T0 + i * SLOT: (200.0 if i < 4 else 3000.0) for i in range(8)}


def _bands(p50: list[float], p10: list[float]) -> list[ForecastSlot]:
    return [
        ForecastSlot(T0 + i * SLOT, p10[i], p50[i], p50[i])
        for i in range(len(p50))
    ]


def _stamp(slots: list[ForecastSlot]) -> list[ForecastSlot]:
    """Same curve, marked the way a real-P10 delivery is stamped."""
    return [replace(slot, real_p10=True) for slot in slots]


def _adaptive(**kw) -> AdaptiveConfig:
    base = dict(
        usable_kwh=10.0, reserve_soc_pct=10.0, round_trip_efficiency=1.0,
        max_charge_w=4000.0, degradation_eur_per_kwh=0.0, risk_margin_eur_per_kwh=0.0,
        solar_confidence=0.8,
    )
    base.update(kw)
    return AdaptiveConfig(**base)


def _summer(**kw) -> SummerConfig:
    base = dict(
        usable_kwh=10.0, target_soc_pct=80.0, round_trip_efficiency=1.0,
        max_charge_w=4000.0, expected_load_w=0.0, allow_grid_topup=True,
        max_topup_price_eur_per_kwh=0.30, degradation_eur_per_kwh=0.0,
        risk_margin_eur_per_kwh=0.0, solar_confidence=0.8,
    )
    base.update(kw)
    return SummerConfig(**base)


def _summer_prices(n: int = 16) -> list[PriceSlot]:
    return [PriceSlot(T0 + i * SLOT, 0.10) for i in range(n)]


def _real_p10_summer() -> list[ForecastSlot]:
    # P10 well under confidence × P50 so a cautious band would bind if summer read it.
    return _bands([1000.0] * 16, [200.0] * 16)


def _real_p10_adaptive() -> list[ForecastSlot]:
    p50 = [2250.0] * 4 + [0.0] * 4
    p10 = [400.0] * 4 + [0.0] * 4
    return _bands(p50, p10)


def test_committed_solar_w_never_exceeds_confidence_times_p50():
    assert committed_solar_w(1000.0, 200.0, 0.8, commit_solar_basis="auto",
                             real_p10=True) == 200.0
    assert committed_solar_w(1000.0, 900.0, 0.8, commit_solar_basis="auto",
                             real_p10=True) == 800.0
    assert committed_solar_w(1000.0, 200.0, 0.8, commit_solar_basis="p50",
                             real_p10=True) == 800.0
    assert committed_solar_w(1000.0, 200.0, 0.8, commit_solar_basis="auto",
                             real_p10=False) == 800.0
    assert commitment_solar_basis("auto", True) == "p10"
    assert commitment_solar_basis("p50", True) == "p50"
    assert commitment_solar_basis("auto", False) is None


def test_real_p10_increases_adaptive_shortfall_and_bought_kwh():
    prices, load, fc = _adaptive_prices(), _adaptive_load(), _real_p10_adaptive()
    p50 = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(),
    )
    p10 = plan_adaptive(
        prices, _stamp(fc), T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(commit_solar_basis="auto"),
    )
    assert p50.buy_pool is not None and p10.buy_pool is not None
    assert p10.buy_pool.shortfall_kwh > p50.buy_pool.shortfall_kwh
    assert _bought(p10) > _bought(p50)
    assert abs(p50.buy_pool.shortfall_kwh - 0.9) < 1e-6
    assert abs(p10.buy_pool.shortfall_kwh - 2.3) < 1e-6
    assert abs(_bought(p50) - 1.0) < 1e-9
    assert abs(_bought(p10) - 3.0) < 1e-9
    assert p10.solar_basis == "p10"
    assert p50.solar_basis is None


def test_summer_keeps_p50_sizing_and_solar_basis_none():
    """PO (b): summer.py is unchanged, so a real-P10 stamp does not buy more."""
    prices, fc = _summer_prices(), _real_p10_summer()
    plain = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_summer())
    stamped = plan_summer(prices, _stamp(fc), T0, soc_pct=50.0, cfg=_summer())
    assert _contract(stamped) == _contract(plain)
    assert _bought(plain) == 0.0
    assert _bought(stamped) == 0.0
    assert plain.solar_basis is None
    assert stamped.solar_basis is None


def test_synthetic_forecast_solar_bands_leave_summer_and_adaptive_unchanged():
    """Derived 0.6× bands without a real-P10 stamp stay on confidence × P50."""
    summer_fc = _bands([1000.0] * 16, [600.0] * 16)
    equal = _bands([1000.0] * 16, [1000.0] * 16)
    synthetic = plan_summer(
        _summer_prices(), summer_fc, T0, soc_pct=50.0, cfg=_summer(),
    )
    baseline = plan_summer(
        _summer_prices(), equal, T0, soc_pct=50.0, cfg=_summer(),
    )
    assert _contract(synthetic) == _contract(baseline)
    assert synthetic.solar_basis is None
    assert all(not slot.real_p10 for slot in summer_fc)

    ad_fc = _bands([2250.0] * 4 + [0.0] * 4, [1350.0] * 4 + [0.0] * 4)
    ad_equal = _bands([2250.0] * 4 + [0.0] * 4, [2250.0] * 4 + [0.0] * 4)
    left = plan_adaptive(
        _adaptive_prices(), ad_fc, T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis="auto"),
    )
    right = plan_adaptive(
        _adaptive_prices(), ad_equal, T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(),
    )
    assert _contract(left) == _contract(right)
    assert left.buy_pool is not None and right.buy_pool is not None
    assert left.buy_pool.shortfall_kwh == right.buy_pool.shortfall_kwh
    assert left.solar_basis is None


def test_commit_solar_basis_p50_matches_today_slot_for_slot():
    fc = _real_p10_adaptive()
    prices, load = _adaptive_prices(), _adaptive_load()
    explicit = plan_adaptive(
        prices, _stamp(fc), T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(commit_solar_basis="p50"),
    )
    untouched = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load, cfg=_adaptive(),
    )
    assert _contract(explicit) == _contract(untouched)
    assert explicit.solar_basis == "p50"
    assert untouched.solar_basis is None

    summer_fc = _real_p10_summer()
    summer_now = plan_summer(
        _summer_prices(), summer_fc, T0, soc_pct=50.0, cfg=_summer(),
    )
    summer_stamped = plan_summer(
        _summer_prices(), _stamp(summer_fc), T0, soc_pct=50.0, cfg=_summer(),
    )
    assert _contract(summer_stamped) == _contract(summer_now)
    assert summer_now.solar_basis is None
    assert summer_stamped.solar_basis is None


def test_solar_basis_reaches_plan_response_and_survives_projection_and_soak():
    adaptive = plan_adaptive(
        _adaptive_prices(), _stamp(_real_p10_adaptive()), T0, soc_pct=15.0,
        load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis="auto"),
    )
    summer = plan_summer(
        _summer_prices(), _stamp(_real_p10_summer()), T0, soc_pct=50.0, cfg=_summer(),
    )
    assert adaptive.solar_basis == "p10"
    assert summer.solar_basis is None

    def _response(plan: Plan) -> dict:
        ctx = ApplicationContext(
            source=None, clock=FrozenClock(T0),
            current_plan=lambda now=None: (T0, [], plan),
            validate_plan=lambda plan, now: PlanValidation("valid"),
            policy=lambda settings: {}, tariff_warnings=lambda settings: [],
        )
        return PlanService(ctx).get_plan(now=T0)

    assert _response(adaptive)["solar_basis"] == "p10"
    assert _response(summer)["solar_basis"] is None
    p50_plan = plan_adaptive(
        _adaptive_prices(), _stamp(_real_p10_adaptive()), T0, soc_pct=15.0,
        load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis="p50"),
    )
    assert _response(p50_plan)["solar_basis"] == "p50"

    slot = PlanSlot(T0, BatteryIntent.DISCHARGE_FOR_LOAD, "shave", power_w=1000.0, floor_soc=10.0)
    held = mark_floor_holds(
        Plan(created_at=T0, slots=(slot,), solar_basis="p10"),
        [ProjectedSlot(
            T0, BatteryIntent.DISCHARGE_FOR_LOAD, soc_pct=10.0, battery_w=0.0,
            grid_w=1000.0, solar_w=0.0, load_w=1000.0,
        )],
        10.0,
        start_soc_pct=10.0,
    )
    assert held.slots[0].intent is BatteryIntent.HOLD_RESERVE
    assert held.solar_basis == "p10"

    soaked = plan_adaptive(
        [PriceSlot(T0, -0.05)] + _adaptive_prices()[1:],
        _stamp(_real_p10_adaptive()), T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis="auto", negative_price_soak=True),
    )
    assert soaked.solar_basis == "p10"
    assert any(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in soaked.slots)


def test_p10_sizing_stays_within_daily_grid_charge_cap():
    fc = _real_p10_adaptive()
    prices, load = _adaptive_prices(), _adaptive_load()
    open_cap = plan_adaptive(
        prices, _stamp(fc), T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(max_daily_grid_charge_kwh=40.0),
    )
    capped = plan_adaptive(
        prices, _stamp(fc), T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(max_daily_grid_charge_kwh=2.0),
    )
    p50 = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(max_daily_grid_charge_kwh=2.0),
    )
    assert abs(_bought(open_cap) - 3.0) < 1e-9
    assert abs(_bought(capped) - 2.0) < 1e-9
    assert _bought(capped) > _bought(p50)
    assert abs(_bought(p50) - 1.0) < 1e-9
    assert capped.solar_basis == "p10"

    summer_plain = plan_summer(
        _summer_prices(), _real_p10_summer(), T0, soc_pct=50.0,
        cfg=_summer(max_daily_grid_charge_kwh=2.0),
    )
    summer_stamped = plan_summer(
        _summer_prices(), _stamp(_real_p10_summer()), T0, soc_pct=50.0,
        cfg=_summer(max_daily_grid_charge_kwh=40.0),
    )
    assert _bought(summer_stamped) == _bought(summer_plain)
    assert _bought(summer_stamped) <= 2.0 + 1e-9
    assert summer_stamped.solar_basis is None


def test_commit_solar_basis_setting_is_auto_or_p50_advanced():
    field = SETTINGS_BY_KEY["planner.commit_solar_basis"]
    assert field.type == "enum"
    assert field.default == "auto"
    assert field.options == ("auto", "p50")
    assert field.advanced is True
    assert defaults()["planner.commit_solar_basis"] == "auto"
    clean, errors = validate_settings({"planner.commit_solar_basis": "p50"})
    assert errors == {} and clean["planner.commit_solar_basis"] == "p50"
    _bad, err = validate_settings({"planner.commit_solar_basis": "p10"})
    assert "planner.commit_solar_basis" in err


def test_delivered_source_real_p10_follows_capabilities_and_solcast_fallback():
    """Slots without the stamp (a Solcast fallback, or unknown) keep auto on P50."""
    fc = _real_p10_adaptive()
    assert fc and all(not slot.real_p10 for slot in fc)
    assert fc[0].p10_w < 0.8 * fc[0].p50_w
    fallen_back = plan_adaptive(
        _adaptive_prices(), fc, T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis="auto"),
    )
    pure_p50 = plan_adaptive(
        _adaptive_prices(), _stamp(fc), T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis="p50"),
    )
    assert _contract(fallen_back) == _contract(pure_p50)
    assert fallen_back.solar_basis is None
    assert pure_p50.solar_basis == "p50"
    assert fallen_back.buy_pool is not None and pure_p50.buy_pool is not None
    assert fallen_back.buy_pool.shortfall_kwh == pure_p50.buy_pool.shortfall_kwh


def test_commit_solar_basis_reaches_live_summer_cfg_and_adaptive_cfg():
    settings = defaults()
    settings["planner.commit_solar_basis"] = "p50"
    svc = ControlService(
        ctx=ControlContext(),
        settings=settings,
        controller=None,
        store=None,
        audit_store=None,
        price_source=None,
        solar_forecast=object(),
        site_tz=AMS,
        dry_run=True,
        data_quality=lambda now: "fresh",
        validate_plan_obj=lambda plan, now: PlanValidation("valid"),
        current_soc=lambda now: 50.0,
    )
    summer = svc.summer_cfg(50.0)
    adaptive = svc.adaptive_cfg()
    assert "commit_solar_basis" not in summer.__dataclass_fields__
    assert "forecast_real_p10" not in summer.__dataclass_fields__
    assert adaptive.commit_solar_basis == "p50"
    assert "forecast_real_p10" not in adaptive.__dataclass_fields__

    svc._solar_forecast = object()
    svc._settings["planner.commit_solar_basis"] = "auto"
    again = svc.summer_cfg(50.0)
    assert "commit_solar_basis" not in again.__dataclass_fields__
    assert svc.adaptive_cfg().commit_solar_basis == "auto"
    assert svc.summer_cfg(50.0).solar_confidence == summer.solar_confidence


def test_replay_passes_commit_solar_basis_and_unknown_forecast_stays_p50():
    cfg = ReplayConfig.from_settings({"planner.commit_solar_basis": "p50"}, tz=AMS)
    assert cfg.commit_solar_basis == "p50"
    assert _adaptive_cfg(cfg).commit_solar_basis == "p50"
    plain = ReplayConfig.from_settings({}, tz=AMS)
    assert plain.commit_solar_basis == "auto"
    assert _adaptive_cfg(plain).commit_solar_basis == "auto"
    summer = _summer_cfg(cfg)
    assert "commit_solar_basis" not in summer.__dataclass_fields__
    assert "forecast_real_p10" not in summer.__dataclass_fields__

    fc = _real_p10_adaptive()
    auto_plan = plan_adaptive(
        _adaptive_prices(), fc, T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis="auto"),
    )
    named = plan_adaptive(
        _adaptive_prices(), fc, T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis=_adaptive_cfg(cfg).commit_solar_basis),
    )
    assert all(not slot.real_p10 for slot in fc)
    assert _contract(auto_plan) == _contract(named)
    assert auto_plan.solar_basis is None
    assert named.solar_basis == "p50"
