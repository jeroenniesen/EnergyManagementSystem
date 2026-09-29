"""Evening-peak coverage probability from calibrated planning scenarios (B-63 / #88).

Given current usable energy above reserve and a set of solar/load scenarios for the expensive
evening window, estimate how likely the battery is to cover that peak without importing at peak
prices. Pure — no I/O, no writes. The live planner/validator path is unchanged; this only
answers the homeowner question for API/UI.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.intelligence.bands import (
    BandCalibration,
    calibrate_bands,
    load_relative_errors_from_pairs,
)
from ems.intelligence.planning import PlanningScenario, build_planning_scenarios
from ems.planner.load_profile import LoadProfile
from ems.sources.forecast import ForecastSlot

SLOT = timedelta(minutes=15)
SLOT_HOURS = SLOT.total_seconds() / 3600.0  # 0.25 h
# Default NL evening window when the caller does not pass explicit peak slots.
_DEFAULT_PEAK_HOURS = range(17, 22)
_DEFAULT_HORIZON_SLOTS = 96


@dataclass(frozen=True)
class PeakCoverage:
    """Probability that stored energy covers tonight's evening peak under calibrated bands."""

    probability: float | None
    available: bool
    label: str
    reason: str
    calibrated: bool
    scenarios_covering: int
    scenarios_total: int
    peak_kwh_expected: float | None
    available_kwh: float | None

    def to_dict(self) -> dict:
        return {
            "probability": (
                None if self.probability is None else round(self.probability, 3)
            ),
            "available": self.available,
            "label": self.label,
            "reason": self.reason,
            "calibrated": self.calibrated,
            "scenarios_covering": self.scenarios_covering,
            "scenarios_total": self.scenarios_total,
            "peak_kwh_expected": (
                None if self.peak_kwh_expected is None
                else round(self.peak_kwh_expected, 3)
            ),
            "available_kwh": (
                None if self.available_kwh is None else round(self.available_kwh, 3)
            ),
        }


def default_evening_peak_starts(
    horizon_starts: list[datetime],
    *,
    tz: ZoneInfo,
    now: datetime,
    peak_hours: range = _DEFAULT_PEAK_HOURS,
) -> list[datetime]:
    """Pick upcoming local evening-peak slot starts from a planning horizon."""
    out: list[datetime] = []
    for start in horizon_starts:
        if start < now:
            continue
        local = start.astimezone(tz)
        if local.hour in peak_hours:
            out.append(start)
    return out


def _net_peak_kwh(scenario: PlanningScenario, peak_starts: list[datetime]) -> float:
    """AC kWh the house still needs from the battery (or grid) during the peak window."""
    total = 0.0
    for start in peak_starts:
        load = max(0.0, float(scenario.load_w_by.get(start, 0.0)))
        solar = max(0.0, float(scenario.solar_w_by.get(start, 0.0)))
        total += max(0.0, load - solar) * SLOT_HOURS / 1000.0
    return total


def _available_discharge_kwh(
    *,
    current_soc_pct: float,
    reserve_soc_pct: float,
    usable_kwh: float,
    round_trip_efficiency: float,
    planned_charge_kwh_before_peak: float = 0.0,
) -> float:
    """Usable AC energy the battery can deliver above reserve before/at the peak."""
    usable = max(0.0, float(usable_kwh))
    if usable <= 0.0:
        return 0.0
    eta = math.sqrt(max(1e-6, min(1.0, float(round_trip_efficiency))))
    soc = max(0.0, min(100.0, float(current_soc_pct)))
    reserve = max(0.0, min(100.0, float(reserve_soc_pct)))
    # Planned grid top-up before the peak adds DC energy at charge efficiency.
    charge = max(0.0, float(planned_charge_kwh_before_peak))
    soc_kwh = soc / 100.0 * usable + charge * eta
    reserve_kwh = reserve / 100.0 * usable
    dc = max(0.0, min(usable, soc_kwh) - reserve_kwh)
    return dc * eta  # discharge to AC


def _label_for(probability: float) -> str:
    pct = int(round(probability * 100))
    if probability >= 0.85:
        return f"Evening peak likely covered (~{pct}%)"
    if probability >= 0.50:
        return f"Evening peak may be covered (~{pct}%)"
    if probability >= 0.20:
        return f"Evening peak coverage uncertain (~{pct}%)"
    return f"Evening peak likely needs grid (~{pct}%)"


def evening_peak_coverage(
    scenarios: tuple[PlanningScenario, ...] | list[PlanningScenario],
    *,
    peak_starts: list[datetime],
    current_soc_pct: float,
    reserve_soc_pct: float,
    usable_kwh: float,
    round_trip_efficiency: float = 0.90,
    planned_charge_kwh_before_peak: float = 0.0,
    calibrated: bool = False,
) -> PeakCoverage:
    """Estimate P(battery covers evening peak) across calibrated planning scenarios.

    Each scenario is a plausible future (pessimistic / expected / optimistic). A scenario
    "covers" when available discharge energy ≥ that scenario's net peak demand. Probability is
    the fraction of scenarios that cover — with the named scenarios treated as an unweighted
    ensemble over the calibrated bands (honest when history is thin: ``calibrated=False`` and
    the reason says so).
    """
    if not peak_starts:
        return PeakCoverage(
            probability=None,
            available=False,
            label="Evening peak coverage unavailable",
            reason="No evening-peak slots in the current planning horizon.",
            calibrated=calibrated,
            scenarios_covering=0,
            scenarios_total=0,
            peak_kwh_expected=None,
            available_kwh=None,
        )
    if not scenarios:
        return PeakCoverage(
            probability=None,
            available=False,
            label="Evening peak coverage unavailable",
            reason="No planning scenarios to evaluate.",
            calibrated=calibrated,
            scenarios_covering=0,
            scenarios_total=0,
            peak_kwh_expected=None,
            available_kwh=None,
        )

    available = _available_discharge_kwh(
        current_soc_pct=current_soc_pct,
        reserve_soc_pct=reserve_soc_pct,
        usable_kwh=usable_kwh,
        round_trip_efficiency=round_trip_efficiency,
        planned_charge_kwh_before_peak=planned_charge_kwh_before_peak,
    )
    nets = [_net_peak_kwh(s, peak_starts) for s in scenarios]
    covering = sum(1 for net in nets if available + 1e-9 >= net)
    total = len(scenarios)
    probability = covering / total
    # Expected (= middle) scenario net demand for the explanation.
    by_name = {s.name: net for s, net in zip(scenarios, nets, strict=False)}
    expected_net = by_name.get("expected", nets[len(nets) // 2])

    if covering == total:
        detail = (
            f"All {total} calibrated scenarios stay above reserve through the evening peak "
            f"(~{expected_net:.1f} kWh needed, ~{available:.1f} kWh available)."
        )
    elif covering == 0:
        detail = (
            f"None of the {total} scenarios cover the evening peak "
            f"(~{expected_net:.1f} kWh needed, ~{available:.1f} kWh available above reserve)."
        )
    else:
        detail = (
            f"{covering} of {total} scenarios cover the evening peak "
            f"(~{expected_net:.1f} kWh needed, ~{available:.1f} kWh available above reserve)."
        )
    if not calibrated:
        detail += " Bands still use defaults until more forecast history is scored."

    return PeakCoverage(
        probability=probability,
        available=True,
        label=_label_for(probability),
        reason=detail,
        calibrated=calibrated,
        scenarios_covering=covering,
        scenarios_total=total,
        peak_kwh_expected=expected_net,
        available_kwh=available,
    )


def estimate_evening_peak_coverage(
    forecast: list[ForecastSlot],
    load_profile: LoadProfile,
    *,
    now: datetime,
    tz: ZoneInfo,
    current_soc_pct: float,
    reserve_soc_pct: float,
    usable_kwh: float,
    round_trip_efficiency: float = 0.90,
    planned_charge_kwh_before_peak: float = 0.0,
    horizon_slots: int = _DEFAULT_HORIZON_SLOTS,
    solar_matched: list[tuple[float, float, float, float]] | None = None,
    load_actual_predicted: list[tuple[float, float]] | None = None,
    calibration: BandCalibration | None = None,
    peak_starts: list[datetime] | None = None,
) -> PeakCoverage:
    """Build calibrated scenarios and score evening-peak coverage in one call (API/UI path)."""
    cal = calibration or calibrate_bands(
        solar_matched=solar_matched,
        load_relative_errors=load_relative_errors_from_pairs(load_actual_predicted or []),
    )
    scenarios = build_planning_scenarios(
        forecast,
        load_profile,
        horizon_slots=horizon_slots,
        calibration=cal,
    )
    starts = peak_starts
    if starts is None:
        horizon = [s.start for s in forecast[: max(0, horizon_slots)]]
        starts = default_evening_peak_starts(horizon, tz=tz, now=now)
    return evening_peak_coverage(
        scenarios,
        peak_starts=starts,
        current_soc_pct=current_soc_pct,
        reserve_soc_pct=reserve_soc_pct,
        usable_kwh=usable_kwh,
        round_trip_efficiency=round_trip_efficiency,
        planned_charge_kwh_before_peak=planned_charge_kwh_before_peak,
        calibrated=cal.calibrated,
    )


def unavailable_peak_coverage(*, reason: str) -> dict:
    """Stable empty contract for paused/stale battery-plan responses."""
    return PeakCoverage(
        probability=None,
        available=False,
        label="Evening peak coverage unavailable",
        reason=reason,
        calibrated=False,
        scenarios_covering=0,
        scenarios_total=0,
        peak_kwh_expected=None,
        available_kwh=None,
    ).to_dict()
