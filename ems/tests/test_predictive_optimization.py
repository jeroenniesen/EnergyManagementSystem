"""Predictive optimization foundation: probabilistic scenarios + risk-aware planning.

These tests cover the first executable slice of E-08/B-63..B-65 without replacing the safe
deterministic planner. The intelligence layer prepares scenario inputs; the existing adaptive
planner still produces and validates the plan.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.intelligence import (
    RiskPolicy,
    build_planning_scenarios,
    plan_risk_aware_adaptive,
)
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.load_profile import build_load_profile
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")
T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)


def _forecast(values: list[tuple[float, float, float]]) -> list[ForecastSlot]:
    return [
        ForecastSlot(T0 + i * SLOT, p10_w=p10, p50_w=p50, p90_w=p90)
        for i, (p10, p50, p90) in enumerate(values)
    ]


def _prices(eur: list[float]) -> list[PriceSlot]:
    return [PriceSlot(T0 + i * SLOT, price) for i, price in enumerate(eur)]


def _learned_profile(hour_load_w: float):
    # T0 is 14:00 local in Amsterdam. Three samples make that hour learned.
    rows = [
        {"ts": "2026-06-20T12:00:00+00:00", "non_ev_load_w": hour_load_w},
        {"ts": "2026-06-20T12:20:00+00:00", "non_ev_load_w": hour_load_w + 100.0},
        {"ts": "2026-06-20T12:40:00+00:00", "non_ev_load_w": hour_load_w + 200.0},
    ]
    return build_load_profile(rows, AMS, min_samples=3)


def _cfg() -> AdaptiveConfig:
    return AdaptiveConfig(
        usable_kwh=10.0,
        reserve_soc_pct=10.0,
        round_trip_efficiency=1.0,
        max_charge_w=4000.0,
        solar_confidence=1.0,
    )


def test_planning_scenarios_preserve_solar_probability_bands_and_names():
    scenarios = build_planning_scenarios(
        _forecast([(200.0, 1000.0, 1600.0), (0.0, 0.0, 0.0)]),
        _learned_profile(700.0),
        horizon_slots=2,
        load_uncertainty=0.20,
    )

    assert [s.name for s in scenarios] == ["pessimistic", "expected", "optimistic"]
    assert scenarios[0].solar_w_by[T0] == 200.0
    assert scenarios[1].solar_w_by[T0] == 1000.0
    assert scenarios[2].solar_w_by[T0] == 1600.0
    assert scenarios[0].confidence == 0.10
    assert scenarios[1].confidence == 0.50
    assert scenarios[2].confidence == 0.90


def test_planning_scenarios_use_learned_load_profile_with_uncertainty_bands():
    scenarios = build_planning_scenarios(
        _forecast([(0.0, 0.0, 0.0)]),
        _learned_profile(700.0),
        horizon_slots=1,
        load_uncertainty=0.20,
    )

    # Learned hour mean is 800 W. Pessimistic planning assumes higher demand; optimistic lower.
    assert scenarios[0].load_w_by[T0] == 960.0
    assert scenarios[1].load_w_by[T0] == 800.0
    assert scenarios[2].load_w_by[T0] == 640.0


def test_risk_aware_planning_uses_conservative_scenario_to_buy_more_before_peak():
    forecast = _forecast(
        # Four cheap pre-peak slots with uncertain solar, then four expensive peak slots.
        [(0.0, 2500.0, 3500.0)] * 4 + [(0.0, 0.0, 0.0)] * 4
    )
    prices = _prices([0.10] * 4 + [0.40] * 4)
    profile = build_load_profile([], AMS)

    expected = plan_risk_aware_adaptive(
        prices,
        forecast,
        T0,
        soc_pct=15.0,
        load_profile=profile,
        cfg=_cfg(),
        policy=RiskPolicy.EXPECTED,
    )
    conservative = plan_risk_aware_adaptive(
        prices,
        forecast,
        T0,
        soc_pct=15.0,
        load_profile=profile,
        cfg=_cfg(),
        policy=RiskPolicy.CONSERVATIVE,
    )

    expected_charge = sum(
        1 for slot in expected.slots if slot.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    )
    conservative_charge = sum(
        1 for slot in conservative.slots if slot.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    )

    assert conservative_charge > expected_charge
    assert conservative.strategy == "adaptive_conservative"
