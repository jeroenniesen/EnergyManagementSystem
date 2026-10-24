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
from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.projection import ProjectedSlot, mark_floor_holds
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.summer import SummerConfig, plan_summer
from ems.planner.validator import PlanValidation
from ems.replay import ReplayConfig, _adaptive_cfg, _summer_cfg
from ems.settings import SETTINGS_BY_KEY, defaults, validate_settings
from ems.sources.forecast import ForecastSlot, commitment_solar_basis, committed_solar_w
from ems.sources.forecast_factory import (
    FORECAST_DOMAIN,
    delivered_source_has_real_p10,
)
from ems.sources.forecast_solar import ForecastSolarSource
from ems.sources.prices import PriceSlot
from ems.sources.registry import get_metadata
from ems.sources.solcast import SolcastSource

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
    # P10 well under confidence × P50 so the cautious band binds.
    return _bands([1000.0] * 16, [200.0] * 16)


def _real_p10_adaptive() -> list[ForecastSlot]:
    p50 = [2250.0] * 4 + [0.0] * 4
    p10 = [400.0] * 4 + [0.0] * 4
    return _bands(p50, p10)


def test_committed_solar_w_never_exceeds_confidence_times_p50():
    assert committed_solar_w(1000.0, 200.0, 0.8, commit_solar_basis="auto",
                             forecast_real_p10=True) == 200.0
    assert committed_solar_w(1000.0, 900.0, 0.8, commit_solar_basis="auto",
                             forecast_real_p10=True) == 800.0
    assert committed_solar_w(1000.0, 200.0, 0.8, commit_solar_basis="p50",
                             forecast_real_p10=True) == 800.0
    assert committed_solar_w(1000.0, 200.0, 0.8, commit_solar_basis="auto",
                             forecast_real_p10=False) == 800.0
    assert commitment_solar_basis("auto", True) == "p10"
    assert commitment_solar_basis("p50", True) == "p50"
    assert commitment_solar_basis("auto", False) is None


def test_real_p10_increases_adaptive_shortfall_and_bought_kwh():
    prices, load, fc = _adaptive_prices(), _adaptive_load(), _real_p10_adaptive()
    p50 = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(forecast_real_p10=False),
    )
    p10 = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(commit_solar_basis="auto", forecast_real_p10=True),
    )
    assert p50.buy_pool is not None and p10.buy_pool is not None
    assert p10.buy_pool.shortfall_kwh > p50.buy_pool.shortfall_kwh
    assert _bought(p10) > _bought(p50)
    assert p10.solar_basis == "p10"
    assert p50.solar_basis is None


def test_real_p10_increases_summer_bought_kwh():
    prices, fc = _summer_prices(), _real_p10_summer()
    p50 = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_summer(forecast_real_p10=False))
    p10 = plan_summer(
        prices, fc, T0, soc_pct=50.0,
        cfg=_summer(commit_solar_basis="auto", forecast_real_p10=True),
    )
    assert _bought(p10) > _bought(p50)
    assert _bought(p50) == 0.0
    assert p10.solar_basis == "p10"


def test_synthetic_forecast_solar_bands_leave_summer_and_adaptive_unchanged():
    """Derived 0.6× bands are not a real P10: auto stays on confidence × P50."""
    summer_fc = _bands([1000.0] * 16, [600.0] * 16)
    equal = _bands([1000.0] * 16, [1000.0] * 16)
    synthetic = plan_summer(
        _summer_prices(), summer_fc, T0, soc_pct=50.0,
        cfg=_summer(forecast_real_p10=False),
    )
    baseline = plan_summer(
        _summer_prices(), equal, T0, soc_pct=50.0, cfg=_summer(),
    )
    assert _contract(synthetic) == _contract(baseline)
    assert synthetic.solar_basis is None

    ad_fc = _bands([2250.0] * 4 + [0.0] * 4, [1350.0] * 4 + [0.0] * 4)
    ad_equal = _bands([2250.0] * 4 + [0.0] * 4, [2250.0] * 4 + [0.0] * 4)
    left = plan_adaptive(
        _adaptive_prices(), ad_fc, T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(forecast_real_p10=False),
    )
    right = plan_adaptive(
        _adaptive_prices(), ad_equal, T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(),
    )
    assert _contract(left) == _contract(right)
    assert left.buy_pool is not None and right.buy_pool is not None
    assert left.buy_pool.shortfall_kwh == right.buy_pool.shortfall_kwh


def test_commit_solar_basis_p50_matches_today_slot_for_slot():
    fc = _real_p10_adaptive()
    prices, load = _adaptive_prices(), _adaptive_load()
    explicit = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(commit_solar_basis="p50", forecast_real_p10=True),
    )
    untouched = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load, cfg=_adaptive(),
    )
    assert _contract(explicit) == _contract(untouched)
    assert explicit.solar_basis == "p50"
    assert untouched.solar_basis is None

    summer_fc = _real_p10_summer()
    summer_p50 = plan_summer(
        _summer_prices(), summer_fc, T0, soc_pct=50.0,
        cfg=_summer(commit_solar_basis="p50", forecast_real_p10=True),
    )
    summer_now = plan_summer(
        _summer_prices(), summer_fc, T0, soc_pct=50.0, cfg=_summer(),
    )
    assert _contract(summer_p50) == _contract(summer_now)
    assert summer_p50.solar_basis == "p50"


def test_solar_basis_reaches_plan_response_and_survives_projection_and_soak():
    adaptive = plan_adaptive(
        _adaptive_prices(), _real_p10_adaptive(), T0, soc_pct=15.0,
        load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis="auto", forecast_real_p10=True),
    )
    summer = plan_summer(
        _summer_prices(), _real_p10_summer(), T0, soc_pct=50.0,
        cfg=_summer(commit_solar_basis="auto", forecast_real_p10=True),
    )
    assert adaptive.solar_basis == "p10"
    assert summer.solar_basis == "p10"

    def _response(plan: Plan) -> dict:
        ctx = ApplicationContext(
            source=None, clock=FrozenClock(T0),
            current_plan=lambda now=None: (T0, [], plan),
            validate_plan=lambda plan, now: PlanValidation("valid"),
            policy=lambda settings: {}, tariff_warnings=lambda settings: [],
        )
        return PlanService(ctx).get_plan(now=T0)

    assert _response(adaptive)["solar_basis"] == "p10"
    assert _response(summer)["solar_basis"] == "p10"
    p50_plan = plan_summer(
        _summer_prices(), _real_p10_summer(), T0, soc_pct=50.0,
        cfg=_summer(commit_solar_basis="p50", forecast_real_p10=True),
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
        _real_p10_adaptive(), T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(
            commit_solar_basis="auto", forecast_real_p10=True, negative_price_soak=True,
        ),
    )
    assert soaked.solar_basis == "p10"
    assert any(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in soaked.slots)


def test_p10_sizing_stays_within_daily_grid_charge_cap():
    fc = _real_p10_adaptive()
    prices, load = _adaptive_prices(), _adaptive_load()
    open_cap = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(forecast_real_p10=True, max_daily_grid_charge_kwh=40.0),
    )
    capped = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(forecast_real_p10=True, max_daily_grid_charge_kwh=2.0),
    )
    p50 = plan_adaptive(
        prices, fc, T0, soc_pct=15.0, load_w_by=load,
        cfg=_adaptive(forecast_real_p10=False, max_daily_grid_charge_kwh=2.0),
    )
    assert _bought(open_cap) > 2.0
    assert _bought(capped) <= 2.0 + 1e-9
    assert _bought(capped) > _bought(p50)
    assert capped.solar_basis == "p10"

    summer_open = plan_summer(
        _summer_prices(), _real_p10_summer(), T0, soc_pct=50.0,
        cfg=_summer(forecast_real_p10=True, max_daily_grid_charge_kwh=40.0),
    )
    summer_cap = plan_summer(
        _summer_prices(), _real_p10_summer(), T0, soc_pct=50.0,
        cfg=_summer(forecast_real_p10=True, max_daily_grid_charge_kwh=2.0),
    )
    assert _bought(summer_open) > 2.0
    assert _bought(summer_cap) <= 2.0 + 1e-9
    assert _bought(summer_cap) > 0.0


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
    solcast = get_metadata(FORECAST_DOMAIN, "solcast")
    forecast_solar = get_metadata(FORECAST_DOMAIN, "forecast_solar")
    mock = get_metadata(FORECAST_DOMAIN, "mock")
    assert solcast is not None and "p10" in solcast.capabilities
    assert "synthetic" not in solcast.capabilities
    assert forecast_solar is not None and "p10" not in forecast_solar.capabilities
    assert mock is not None and "synthetic" in mock.capabilities
    assert "fallback_on_failure" in solcast.capabilities

    assert delivered_source_has_real_p10(None) is False

    class _Solcast:
        provider = "solcast"
        source_label = "solcast"

    class _Fallback:
        provider = "forecast_solar"
        source_label = "forecast.solar (fallback)"

    assert delivered_source_has_real_p10(_Solcast()) is True
    assert delivered_source_has_real_p10(_Fallback()) is False
    assert delivered_source_has_real_p10(ForecastSolarSource(
        tz=AMS, lat=52.1, lon=5.2, tilt=35.0, azimuth=0.0, kwp=3.0,
        http_get=lambda url: {"result": {"watts": {"2026-06-28 12:00:00": 1000}}},
    )) is False

    def _down(url, headers):
        raise RuntimeError("solcast down")

    class _SyntheticBands:
        source_label = "forecast.solar"

        def slots(self):
            return [ForecastSlot(T0, p10_w=600.0, p50_w=1000.0, p90_w=1150.0)]

    source = SolcastSource(
        tz=AMS, api_key="test-key", resource_id="test-resource",
        http_get=_down, fallback=_SyntheticBands(),
        clock=lambda: T0,
    )
    got = source.slots()
    assert got and got[0].p10_w < got[0].p50_w
    assert "fallback" in source.source_label
    assert delivered_source_has_real_p10(source) is False

    # auto after that fallback sizes on P50, even though the slots carry a lower P10.
    fc = _real_p10_adaptive()
    fallen_back = plan_adaptive(
        _adaptive_prices(), fc, T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(
            commit_solar_basis="auto",
            forecast_real_p10=delivered_source_has_real_p10(source),
        ),
    )
    pure_p50 = plan_adaptive(
        _adaptive_prices(), fc, T0, soc_pct=15.0, load_w_by=_adaptive_load(),
        cfg=_adaptive(commit_solar_basis="p50", forecast_real_p10=True),
    )
    assert _contract(fallen_back) == _contract(pure_p50)
    assert fallen_back.buy_pool is not None and pure_p50.buy_pool is not None
    assert fallen_back.buy_pool.shortfall_kwh == pure_p50.buy_pool.shortfall_kwh


def test_commit_solar_basis_reaches_live_summer_cfg_and_adaptive_cfg():
    settings = defaults()
    settings["planner.commit_solar_basis"] = "p50"

    class _Solcast:
        provider = "solcast"
        source_label = "solcast"

    svc = ControlService(
        ctx=ControlContext(),
        settings=settings,
        controller=None,
        store=None,
        audit_store=None,
        price_source=None,
        solar_forecast=_Solcast(),
        site_tz=AMS,
        dry_run=True,
        data_quality=lambda now: "fresh",
        validate_plan_obj=lambda plan, now: PlanValidation("valid"),
        current_soc=lambda now: 50.0,
    )
    summer = svc.summer_cfg(50.0)
    adaptive = svc.adaptive_cfg()
    assert summer.commit_solar_basis == "p50"
    assert adaptive.commit_solar_basis == "p50"
    assert summer.forecast_real_p10 is True
    assert adaptive.forecast_real_p10 is True

    class _AfterFallback:
        provider = "forecast_solar"
        source_label = "forecast.solar (fallback)"

    svc._solar_forecast = _AfterFallback()
    svc._settings["planner.commit_solar_basis"] = "auto"
    assert svc.summer_cfg(50.0).forecast_real_p10 is False
    assert svc.summer_cfg(50.0).commit_solar_basis == "auto"
    assert svc.adaptive_cfg().forecast_real_p10 is False
    assert svc.adaptive_cfg().commit_solar_basis == "auto"


def test_replay_passes_commit_solar_basis_and_unknown_forecast_stays_p50():
    cfg = ReplayConfig.from_settings({"planner.commit_solar_basis": "p50"}, tz=AMS)
    assert cfg.commit_solar_basis == "p50"
    assert cfg.forecast_real_p10 is False
    assert _adaptive_cfg(cfg).commit_solar_basis == "p50"
    assert _adaptive_cfg(cfg).forecast_real_p10 is False
    assert _summer_cfg(cfg).commit_solar_basis == "p50"
    assert _summer_cfg(cfg).forecast_real_p10 is False
    armed = replace(cfg, commit_solar_basis="auto", forecast_real_p10=True)
    assert _adaptive_cfg(armed).forecast_real_p10 is True
    assert _summer_cfg(armed).commit_solar_basis == "auto"
