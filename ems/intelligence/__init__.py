"""Prediction + optimization boundary for EMS intelligence work.

The intelligence layer prepares probabilistic planning inputs. It deliberately delegates execution
planning to the deterministic planner/validator path so model uncertainty can improve decisions
without becoming a safety authority.
"""
from .bands import (
    BandCalibration,
    apply_solar_calibration,
    calibrate_bands,
    load_band_w,
    load_relative_errors_from_pairs,
)
from .peak_coverage import (
    PeakCoverage,
    default_evening_peak_starts,
    estimate_evening_peak_coverage,
    evening_peak_coverage,
    unavailable_peak_coverage,
)
from .planning import (
    PlanningScenario,
    RiskPolicy,
    build_planning_scenarios,
    plan_risk_aware_adaptive,
)

__all__ = [
    "BandCalibration",
    "PeakCoverage",
    "PlanningScenario",
    "RiskPolicy",
    "apply_solar_calibration",
    "build_planning_scenarios",
    "calibrate_bands",
    "default_evening_peak_starts",
    "estimate_evening_peak_coverage",
    "evening_peak_coverage",
    "load_band_w",
    "load_relative_errors_from_pairs",
    "plan_risk_aware_adaptive",
    "unavailable_peak_coverage",
]
