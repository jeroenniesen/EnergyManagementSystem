"""B-63 / #88 — calibrated solar/load confidence bands + evening-peak coverage probability.

Mocks/fakes only — no real devices. Builds on the first E-08 slice (`build_planning_scenarios`)
and reuses empirical load ``band_w`` uncertainty from the B-64 / PR #65 adaptation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ems.intelligence import (
    BandCalibration,
    apply_solar_calibration,
    build_planning_scenarios,
    calibrate_bands,
    default_evening_peak_starts,
    estimate_evening_peak_coverage,
    evening_peak_coverage,
    load_band_w,
    load_relative_errors_from_pairs,
)
from ems.planner.load_profile import build_load_profile
from ems.sources.forecast import ForecastSlot

AMS = ZoneInfo("Europe/Amsterdam")
T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)  # 14:00 local
SLOT = timedelta(minutes=15)
# Local evening peak hours for 2026-06-28 in Amsterdam (CEST = UTC+2): 17:00–21:45 local
# → 15:00–19:45 UTC
PEAK0 = datetime(2026, 6, 28, 15, 0, tzinfo=UTC)


def _forecast(values: list[tuple[float, float, float]], start: datetime = T0) -> list[ForecastSlot]:
    return [
        ForecastSlot(start + i * SLOT, p10_w=p10, p50_w=p50, p90_w=p90)
        for i, (p10, p50, p90) in enumerate(values)
    ]


def _learned_profile(hour_load_w: float):
    rows = [
        {"ts": "2026-06-20T12:00:00+00:00", "non_ev_load_w": hour_load_w},
        {"ts": "2026-06-20T12:20:00+00:00", "non_ev_load_w": hour_load_w + 100.0},
        {"ts": "2026-06-20T12:40:00+00:00", "non_ev_load_w": hour_load_w + 200.0},
    ]
    return build_load_profile(rows, AMS, min_samples=3)


def _daytime_matches(n: int, *, actual_mult: float) -> list[tuple[float, float, float, float]]:
    """n daytime matched slots where actual = expected * actual_mult (provider low/high ignored)."""
    expected = 1000.0
    actual = expected * actual_mult
    return [(actual, expected * 0.6, expected, expected * 1.15) for _ in range(n)]


def test_calibrate_bands_defaults_without_history():
    cal = calibrate_bands()
    assert cal.calibrated is False
    assert cal.solar_low_mult == pytest.approx(0.60)
    assert cal.solar_high_mult == pytest.approx(1.15)
    assert cal.load_uncertainty == pytest.approx(0.15)
    assert "not enough" in cal.reason.lower()


def test_calibrate_bands_from_solar_forecast_residuals():
    # Actuals cluster around 0.7×–1.3× expected → calibrated P10/P90 track that spread.
    matched = (
        _daytime_matches(12, actual_mult=0.70)
        + _daytime_matches(12, actual_mult=1.30)
    )
    cal = calibrate_bands(solar_matched=matched)
    assert cal.calibrated is True
    assert cal.solar_samples >= 24
    assert cal.solar_low_mult == pytest.approx(0.70, abs=0.05)
    assert cal.solar_high_mult == pytest.approx(1.30, abs=0.05)


def test_calibrate_bands_from_load_relative_errors():
    errs = load_relative_errors_from_pairs(
        [(800.0, 1000.0)] * 12 + [(1200.0, 1000.0)] * 12
    )
    cal = calibrate_bands(load_relative_errors=errs)
    assert cal.calibrated is True
    assert cal.load_samples >= 24
    # |−0.2| and |+0.2| → p80 ≈ 0.20
    assert cal.load_uncertainty == pytest.approx(0.20, abs=0.05)


def test_apply_solar_calibration_rewrites_provider_bands_when_calibrated():
    fc = _forecast([(200.0, 1000.0, 1600.0)])
    cal = BandCalibration(
        solar_low_mult=0.75, solar_high_mult=1.25, load_uncertainty=0.15,
        calibrated=True, solar_samples=30, load_samples=0, reason="test",
    )
    out = apply_solar_calibration(fc, cal)
    assert out[0].p50_w == 1000.0
    assert out[0].p10_w == pytest.approx(750.0)
    assert out[0].p90_w == pytest.approx(1250.0)


def test_apply_solar_calibration_keeps_provider_bands_without_solar_evidence():
    fc = _forecast([(200.0, 1000.0, 1600.0)])
    cal = calibrate_bands()  # uncalibrated defaults
    out = apply_solar_calibration(fc, cal)
    assert out[0].p10_w == 200.0
    assert out[0].p50_w == 1000.0
    assert out[0].p90_w == 1600.0


def test_build_planning_scenarios_use_calibrated_solar_and_load_bands():
    cal = BandCalibration(
        solar_low_mult=0.5, solar_high_mult=1.5, load_uncertainty=0.25,
        calibrated=True, solar_samples=30, load_samples=30, reason="test",
    )
    scenarios = build_planning_scenarios(
        _forecast([(200.0, 1000.0, 1600.0)]),
        _learned_profile(700.0),
        horizon_slots=1,
        calibration=cal,
    )
    assert scenarios[0].solar_w_by[T0] == pytest.approx(500.0)  # 1000 * 0.5
    assert scenarios[1].solar_w_by[T0] == pytest.approx(1000.0)
    assert scenarios[2].solar_w_by[T0] == pytest.approx(1500.0)  # 1000 * 1.5
    # Learned mean 800 W ± calibrated ±25%
    assert scenarios[0].load_w_by[T0] == pytest.approx(1000.0)
    assert scenarios[1].load_w_by[T0] == pytest.approx(800.0)
    assert scenarios[2].load_w_by[T0] == pytest.approx(600.0)


def test_load_band_w_prefers_empirical_profile_uncertainty():
    """PR #65 / B-64 empirical spread wins over the calibrated relative half-width."""
    rows = []
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    for i in range(28):
        ts = now - timedelta(days=i + 1)
        # Wide weekend vs weekday so enhanced uncertainty is non-trivial.
        rows.append({"ts": ts.isoformat(), "non_ev_load_w": 900 if ts.weekday() >= 5 else 300})
    profile = build_load_profile(rows, ZoneInfo("UTC"), enhanced=True, as_of=now)
    low, exp, high = load_band_w(profile, now, uncertainty=0.01)
    assert low <= exp <= high
    # Empirical band must be wider than the tiny calibrated 1% half-width.
    assert high - exp > exp * 0.01


def test_evening_peak_coverage_probability_all_scenarios_cover():
    # Flat 400 W load, zero solar during peak; 5 kWh available above reserve easily covers.
    horizon = [PEAK0 + i * SLOT for i in range(8)]
    scenarios = build_planning_scenarios(
        [ForecastSlot(s, 0.0, 0.0, 0.0) for s in horizon],
        build_load_profile([], AMS),  # typical evening ~900 W
        horizon_slots=8,
    )
    cov = evening_peak_coverage(
        scenarios,
        peak_starts=horizon,
        current_soc_pct=80.0,
        reserve_soc_pct=10.0,
        usable_kwh=10.0,
        round_trip_efficiency=1.0,
        calibrated=True,
    )
    assert cov.available is True
    assert cov.probability == pytest.approx(1.0)
    assert cov.scenarios_covering == 3
    assert "likely covered" in cov.label.lower()


def test_evening_peak_coverage_probability_none_cover_when_empty():
    horizon = [PEAK0 + i * SLOT for i in range(8)]
    scenarios = build_planning_scenarios(
        [ForecastSlot(s, 0.0, 0.0, 0.0) for s in horizon],
        build_load_profile([], AMS),
        horizon_slots=8,
    )
    cov = evening_peak_coverage(
        scenarios,
        peak_starts=horizon,
        current_soc_pct=10.0,  # at reserve — nothing to discharge
        reserve_soc_pct=10.0,
        usable_kwh=10.0,
        round_trip_efficiency=1.0,
        calibrated=False,
    )
    assert cov.probability == pytest.approx(0.0)
    assert cov.scenarios_covering == 0
    label = cov.label.lower()
    assert "grid" in label or "uncertain" in label or "needs" in label


def test_evening_peak_coverage_partial_scenarios():
    """Full battery covers expected/optimistic but not a very pessimistic high-load path."""
    horizon = [PEAK0 + i * SLOT for i in range(12)]  # 3 h
    # Force a huge load uncertainty so pessimistic demand exceeds available energy.
    cal = BandCalibration(
        solar_low_mult=0.6, solar_high_mult=1.15, load_uncertainty=0.60,
        calibrated=True, solar_samples=30, load_samples=30, reason="test",
    )
    # Custom profile with 2000 W expected evening load via hour samples at 17:00 local (=15:00 UTC).
    rows = [
        {"ts": (PEAK0 - timedelta(days=d)).isoformat(), "non_ev_load_w": 2000.0}
        for d in range(1, 5)
        for _ in range(3)
    ]
    profile = build_load_profile(rows, AMS, min_samples=3)
    scenarios = build_planning_scenarios(
        [ForecastSlot(s, 0.0, 0.0, 0.0) for s in horizon],
        profile,
        horizon_slots=12,
        calibration=cal,
    )
    # ~2.1 kWh available above 10% reserve on 10 kWh pack at 31% SoC.
    cov = evening_peak_coverage(
        scenarios,
        peak_starts=horizon,
        current_soc_pct=31.0,
        reserve_soc_pct=10.0,
        usable_kwh=10.0,
        round_trip_efficiency=1.0,
        calibrated=True,
    )
    assert cov.available is True
    assert 0.0 < cov.probability < 1.0
    assert 0 < cov.scenarios_covering < 3


def test_default_evening_peak_starts_uses_local_hours():
    starts = [T0 + i * SLOT for i in range(40)]  # through local evening
    peaks = default_evening_peak_starts(starts, tz=AMS, now=T0)
    assert peaks
    assert all(s.astimezone(AMS).hour in range(17, 22) for s in peaks)
    assert all(s >= T0 for s in peaks)


def test_estimate_evening_peak_coverage_end_to_end_with_calibration():
    # Enough daytime residual history to mark calibrated=True.
    matched = _daytime_matches(30, actual_mult=1.0)
    # Horizon spanning into local evening.
    values = [(0.0, 0.0, 0.0)] * 40
    fc = _forecast(values, start=T0)
    cov = estimate_evening_peak_coverage(
        fc,
        build_load_profile([], AMS),
        now=T0,
        tz=AMS,
        current_soc_pct=90.0,
        reserve_soc_pct=10.0,
        usable_kwh=10.0,
        round_trip_efficiency=1.0,
        solar_matched=matched,
    )
    assert cov.available is True
    assert cov.calibrated is True
    assert cov.probability is not None
    assert 0.0 <= cov.probability <= 1.0
    payload = cov.to_dict()
    assert set(payload) >= {
        "probability", "available", "label", "reason", "calibrated",
        "scenarios_covering", "scenarios_total",
    }
