"""Calibrate solar/load confidence bands from historical forecast error (B-63 / #88).

Pure helpers — no I/O, no battery writes. Provider P10/P50/P90 (and the learned load profile)
remain the starting point; historical residuals only widen/narrow the bands so that planner
inputs reflect observed forecast skill rather than a fixed ±15% guess.

Reuse note: empirical load spread (`LoadProfile.band_w` / `uncertainty`) was introduced via the
B-64 slice adapted from draft PR #65. This module turns that spread — plus solar forecast
residuals — into calibrated low/expected/high paths for `build_planning_scenarios`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ems.planner.load_profile import LoadProfile
from ems.sources.forecast import ForecastSlot

# Daytime floor: ratios against near-zero expected watts are unstable (dawn/dusk).
_MIN_EXPECTED_W = 50.0
# Need enough matched daytime slots before trusting a residual-derived scale.
_MIN_SOLAR_SAMPLES = 24
_MIN_LOAD_SAMPLES = 24
# Default multiplicative solar bands when history is thin (matches Forecast.Solar-style fallback).
_DEFAULT_SOLAR_LOW_MULT = 0.60
_DEFAULT_SOLAR_HIGH_MULT = 1.15
# Default relative load half-width when history is thin (previous hard-coded scenario width).
_DEFAULT_LOAD_UNCERTAINTY = 0.15
# Keep calibrated load bands honest but finite.
_MIN_LOAD_UNCERTAINTY = 0.05
_MAX_LOAD_UNCERTAINTY = 0.60


@dataclass(frozen=True)
class BandCalibration:
    """Scales that turn an expected value into calibrated low/high bands.

    Solar: ``low = expected * solar_low_mult``, ``high = expected * solar_high_mult``
    (clamped ≥ 0). Load: ``low = expected * (1 - load_uncertainty)``,
    ``high = expected * (1 + load_uncertainty)``.

    ``calibrated`` is True only when enough historical residuals were available to estimate the
    scales; otherwise defaults apply and the UI must not claim "learned from your roof".
    """

    solar_low_mult: float
    solar_high_mult: float
    load_uncertainty: float
    calibrated: bool
    solar_samples: int
    load_samples: int
    reason: str


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank percentile on an already-sorted non-empty list (``p`` in 0..1)."""
    n = len(sorted_vals)
    idx = max(0, min(n - 1, math.ceil(p * n) - 1))
    return sorted_vals[idx]


def _finite(values: list[float]) -> list[float]:
    return [v for v in values if math.isfinite(v)]


def calibrate_bands(
    *,
    solar_matched: list[tuple[float, float, float, float]] | None = None,
    load_relative_errors: list[float] | None = None,
) -> BandCalibration:
    """Estimate band scales from historical forecast residuals.

    ``solar_matched`` entries are ``(actual_w, low_w, expected_w, high_w)`` — the same shape
    ``ems.analysis._matched_slots`` returns. Only daytime-ish expected watts contribute to the
    multiplicative scales (dawn/dusk excluded).

    ``load_relative_errors`` are ``(actual - predicted) / max(predicted, floor)`` for held-out
    hours. The load half-width is the 80%-coverage absolute relative error (p80 of |error|).
    """
    solar = solar_matched or []
    load_errs = load_relative_errors or []

    ratios = _finite([
        actual / expected
        for actual, _low, expected, _high in solar
        if expected >= _MIN_EXPECTED_W and actual >= 0.0
    ])
    solar_ok = len(ratios) >= _MIN_SOLAR_SAMPLES
    if solar_ok:
        ratios_sorted = sorted(ratios)
        low_mult = max(0.05, min(1.0, _percentile(ratios_sorted, 0.10)))
        high_mult = max(1.0, min(3.0, _percentile(ratios_sorted, 0.90)))
        # Keep a non-zero width even when history is oddly tight.
        if high_mult - low_mult < 0.05:
            high_mult = low_mult + 0.05
    else:
        low_mult, high_mult = _DEFAULT_SOLAR_LOW_MULT, _DEFAULT_SOLAR_HIGH_MULT

    abs_rel = _finite([abs(e) for e in load_errs])
    load_ok = len(abs_rel) >= _MIN_LOAD_SAMPLES
    if load_ok:
        abs_sorted = sorted(abs_rel)
        load_u = _percentile(abs_sorted, 0.80)
        load_u = max(_MIN_LOAD_UNCERTAINTY, min(_MAX_LOAD_UNCERTAINTY, load_u))
    else:
        load_u = _DEFAULT_LOAD_UNCERTAINTY

    calibrated = solar_ok or load_ok
    parts: list[str] = []
    if solar_ok:
        parts.append(f"solar bands from {len(ratios)} daytime forecast residuals")
    if load_ok:
        parts.append(f"load bands from {len(abs_rel)} held-out relative errors")
    if not parts:
        parts.append(
            "not enough forecast history yet — using default solar ± and ±15% load bands"
        )
    return BandCalibration(
        solar_low_mult=round(low_mult, 4),
        solar_high_mult=round(high_mult, 4),
        load_uncertainty=round(load_u, 4),
        calibrated=calibrated,
        solar_samples=len(ratios),
        load_samples=len(abs_rel),
        reason="; ".join(parts),
    )


def apply_solar_calibration(
    forecast: list[ForecastSlot],
    calibration: BandCalibration,
) -> list[ForecastSlot]:
    """Rewrite each slot's P10/P90 around P50 using the calibrated multipliers.

    P50 (expected) is unchanged — only the confidence band width is evidence-driven. Ordering
    ``low <= expected <= high`` is enforced after scaling.
    """
    out: list[ForecastSlot] = []
    for slot in forecast:
        expected = max(0.0, float(slot.p50_w))
        if calibration.calibrated and calibration.solar_samples >= _MIN_SOLAR_SAMPLES:
            low = max(0.0, expected * calibration.solar_low_mult)
            high = max(low, expected * calibration.solar_high_mult)
        else:
            # Keep provider bands when solar residual history is thin.
            low = max(0.0, float(slot.p10_w))
            high = max(low, float(slot.p90_w))
            # Still guarantee ordering around expected.
            low = min(low, expected)
            high = max(high, expected)
        out.append(ForecastSlot(start=slot.start, p10_w=low, p50_w=expected, p90_w=high))
    return out


def load_band_w(
    profile: LoadProfile,
    when: datetime,
    *,
    uncertainty: float,
    weather_bin: str | None = None,
    daytime_solar_w: float | None = None,
) -> tuple[float, float, float]:
    """Return ``(low, expected, high)`` load watts for a slot.

    Prefer the profile's empirical ``band_w`` (PR #65 / B-64) when that hour has learned
    uncertainty; otherwise fall back to the calibrated relative half-width.
    """
    expected = profile.expected_w(
        when, weather_bin=weather_bin, daytime_solar_w=daytime_solar_w,
    )
    local = when.astimezone(profile.tz)
    key = (local.weekday() >= 5, local.hour)
    if key in profile.uncertainty:
        low, high = profile.band_w(
            when, weather_bin=weather_bin, daytime_solar_w=daytime_solar_w,
        )
        return max(0.0, low), expected, max(expected, high)
    u = max(0.0, uncertainty)
    return max(0.0, expected * (1.0 - u)), expected, expected * (1.0 + u)


def load_relative_errors_from_pairs(
    pairs: list[tuple[float, float]],
    *,
    floor_w: float = 50.0,
) -> list[float]:
    """Build relative errors from ``(actual_w, predicted_w)`` pairs for ``calibrate_bands``."""
    out: list[float] = []
    for actual, predicted in pairs:
        try:
            a = float(actual)
            p = float(predicted)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(a) and math.isfinite(p)) or a < 0.0 or p < 0.0:
            continue
        out.append((a - p) / max(p, floor_w))
    return out
