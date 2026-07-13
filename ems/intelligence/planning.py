"""Probabilistic planning inputs for the adaptive battery planner.

This is the first executable slice of E-08:
- B-63: preserve solar forecast bands as named planning scenarios.
- B-64: turn the learned household load profile into per-slot planning demand.
- B-65: select a risk policy before delegating to the deterministic adaptive planner.

The module is pure. It does not read devices, write controls, or validate safety; those remain in
the existing planner/validator path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.load_profile import LoadProfile
from ems.planner.schedule import Plan
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot


@dataclass(frozen=True)
class PlanningScenario:
    """One possible future the planner can evaluate.

    `confidence` is the percentile-like label of the solar band used for this scenario; it is not a
    probability weight. P10/P50/P90 are easier to explain to users as
    pessimistic/expected/optimistic futures than as a hidden ML score.
    """

    name: str
    confidence: float
    solar_w_by: dict[datetime, float]
    load_w_by: dict[datetime, float]


class RiskPolicy(StrEnum):
    CONSERVATIVE = "conservative"
    EXPECTED = "expected"
    OPTIMISTIC = "optimistic"


_POLICY_TO_SCENARIO = {
    RiskPolicy.CONSERVATIVE: "pessimistic",
    RiskPolicy.EXPECTED: "expected",
    RiskPolicy.OPTIMISTIC: "optimistic",
}


def _scenario_forecast(
    forecast: list[ForecastSlot], solar_w_by: dict[datetime, float]
) -> list[ForecastSlot]:
    """Adapt a scenario's solar path into the current planner contract.

    `plan_adaptive` reads `ForecastSlot.p50_w`, so this keeps the original slot timestamps and uses
    the selected scenario path as the effective expected solar value.
    """
    return [
        ForecastSlot(start=slot.start, p10_w=solar_w_by[slot.start],
                     p50_w=solar_w_by[slot.start], p90_w=solar_w_by[slot.start])
        for slot in forecast
        if slot.start in solar_w_by
    ]


def _with_strategy(plan: Plan, strategy: str) -> Plan:
    return Plan(
        created_at=plan.created_at,
        slots=plan.slots,
        strategy=strategy,
        target_soc=plan.target_soc,
        deadline=plan.deadline,
    )


def build_planning_scenarios(
    forecast: list[ForecastSlot],
    load_profile: LoadProfile,
    *,
    horizon_slots: int,
    load_uncertainty: float = 0.15,
) -> tuple[PlanningScenario, PlanningScenario, PlanningScenario]:
    """Build pessimistic/expected/optimistic planning scenarios from solar bands + learned load.

    Pessimistic = low solar (P10) + higher load. Expected = P50 + learned load. Optimistic = high
    solar (P90) + lower load. This is intentionally simple and deterministic; historical forecast
    error can later tune the band widths without changing the planner interface.
    """
    limited = forecast[: max(0, horizon_slots)]
    uncertainty = max(0.0, load_uncertainty)

    expected_load = {slot.start: load_profile.expected_w(slot.start) for slot in limited}
    return (
        PlanningScenario(
            name="pessimistic",
            confidence=0.10,
            solar_w_by={slot.start: max(0.0, slot.p10_w) for slot in limited},
            load_w_by={start: watts * (1.0 + uncertainty)
                       for start, watts in expected_load.items()},
        ),
        PlanningScenario(
            name="expected",
            confidence=0.50,
            solar_w_by={slot.start: max(0.0, slot.p50_w) for slot in limited},
            load_w_by=expected_load,
        ),
        PlanningScenario(
            name="optimistic",
            confidence=0.90,
            solar_w_by={slot.start: max(0.0, slot.p90_w) for slot in limited},
            load_w_by={start: max(0.0, watts * (1.0 - uncertainty))
                       for start, watts in expected_load.items()},
        ),
    )


def plan_risk_aware_adaptive(
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    now: datetime,
    *,
    soc_pct: float,
    load_profile: LoadProfile,
    cfg: AdaptiveConfig,
    policy: RiskPolicy = RiskPolicy.EXPECTED,
    load_uncertainty: float = 0.15,
) -> Plan:
    """Plan against the scenario selected by `policy`, then delegate to the adaptive planner.

    This adds risk awareness without introducing a second control algorithm. The existing adaptive
    planner still determines charge/discharge slots and the validator remains the safety authority.
    """
    scenarios = build_planning_scenarios(
        forecast,
        load_profile,
        horizon_slots=cfg.horizon_slots,
        load_uncertainty=load_uncertainty,
    )
    scenario_name = _POLICY_TO_SCENARIO[policy]
    scenario = next(s for s in scenarios if s.name == scenario_name)
    plan = plan_adaptive(
        prices,
        _scenario_forecast(forecast[: cfg.horizon_slots], scenario.solar_w_by),
        now,
        soc_pct=soc_pct,
        load_w_by=scenario.load_w_by,
        cfg=cfg,
    )
    return _with_strategy(plan, f"adaptive_{policy.value}")
