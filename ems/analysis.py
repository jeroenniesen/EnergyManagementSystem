"""Forecast skill: how well the day-ahead solar forecast matched what actually happened.

Pure — no clock, no I/O. The export endpoint hands in the stored forecast snapshots and raw
samples; this buckets actual solar into the same 15-min slots as the forecast and scores the
match. This is what makes the forecast logging (forecast_snapshots / forecasts.csv) immediately
useful, and later lets `planner.solar_confidence` be tuned from evidence instead of guesswork.

Scope: forecast error ONLY. A plan's `target_soc` is a deadline target, not an instantaneous
setpoint, so a naive SoC-vs-target gap is misleading (SoC is legitimately below target while
still charging toward it, ahead of the deadline). That "plan adherence" metric is a noted future
item, not attempted here.
"""
from __future__ import annotations

import math
from collections import defaultdict

from ems.retrospect import _floor, _mean, _parse

_DH = 15 / 60.0  # hours per 15-min slot


def _matched_slots(
    forecast_rows: list[dict], raw_rows: list[dict]
) -> list[tuple[float, float, float, float]]:
    """Bucket actual solar (raw_rows) into 15-min slots and pair each forecast row (`start,
    p10_w, p50_w, p90_w`) with its matched actual — the shared bucketing behind both
    `forecast_error` and `recommend_solar_confidence`, so they score the exact same slots.

    Returns a list of `(actual_w, p10_w, p50_w, p90_w)` tuples, one per slot where both a
    forecast and at least one raw sample exist; a forecast slot with no recorded actual is
    skipped (not fabricated).
    """
    actual_by_slot: dict[object, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None:
            continue
        actual_by_slot[_floor(dt)].append(float(r.get("solar_power_w", 0.0)))

    matched: list[tuple[float, float, float, float]] = []
    for row in forecast_rows:
        dt = _parse(row.get("start"))
        if dt is None:
            continue
        samples = actual_by_slot.get(_floor(dt))
        if not samples:
            continue  # no actual recorded for this forecast slot — skip, don't fabricate a match
        actual = _mean(samples)
        p50 = float(row.get("p50_w", 0.0))
        p10 = float(row.get("p10_w", 0.0))
        p90 = float(row.get("p90_w", 0.0))
        matched.append((actual, p10, p50, p90))
    return matched


def forecast_error(forecast_rows: list[dict], raw_rows: list[dict]) -> dict:
    """Bucket actual solar (raw_rows) into 15-min slots and compare against the forecast
    (forecast_rows, each `start, p10_w, p50_w, p90_w`) over the slots where both exist.

    Returns:
        n_slots: matched slot count (0 if forecast and actuals never overlap).
        bias_w: mean(actual − p50) — negative means the forecast over-predicts solar.
        mae_w: mean(|actual − p50|).
        band_coverage_pct: % of matched slots where p10 <= actual <= p90.
        actual_solar_kwh / forecast_p50_kwh: energy over the matched slots only.
    """
    matched = _matched_slots(forecast_rows, raw_rows)
    n_slots = len(matched)
    if n_slots == 0:
        return {
            "n_slots": 0,
            "bias_w": None,
            "mae_w": None,
            "band_coverage_pct": None,
            "actual_solar_kwh": None,
            "forecast_p50_kwh": None,
        }

    errors = [actual - p50 for actual, _p10, p50, _p90 in matched]
    abs_errors = [abs(e) for e in errors]
    in_band = sum(1 for actual, p10, _p50, p90 in matched if p10 <= actual <= p90)
    actual_kwh = sum(actual * _DH / 1000.0 for actual, _p10, _p50, _p90 in matched)
    forecast_kwh = sum(p50 * _DH / 1000.0 for _actual, _p10, p50, _p90 in matched)

    return {
        "n_slots": n_slots,
        "bias_w": round(_mean(errors), 1),
        "mae_w": round(_mean(abs_errors), 1),
        "band_coverage_pct": round(in_band / n_slots * 100.0, 1),
        "actual_solar_kwh": round(actual_kwh, 2),
        "forecast_p50_kwh": round(forecast_kwh, 2),
    }


_MIN_DAYTIME_W = 200.0  # p50 floor for a slot to count as "real daytime" (excludes dawn/dusk noise)
_MIN_SLOTS = 48  # ~a few days' worth of matched daytime slots before a recommendation is trusted


def _percentile(sorted_vals: list[float], p: float) -> float:
    """The `p`-th percentile (0..1) of an already-sorted list, nearest-rank method: the
    `ceil(p * n)`-th smallest value. Deterministic and simple to reason about for a small, discrete
    settings knob — no interpolation between samples."""
    n = len(sorted_vals)
    idx = max(0, min(n - 1, math.ceil(p * n) - 1))
    return sorted_vals[idx]


def recommend_solar_confidence(
    forecast_rows: list[dict], raw_rows: list[dict], *, current_pct: float | None = None
) -> dict | None:
    """Recommend a value for `planner.solar_confidence` from logged day-ahead forecast
    performance, over daytime slots (`p50_w >= 200 W`) matched the same way as `forecast_error`.

    Why the 25th percentile (not the mean/median): `solar_confidence` scales P50 down to a "safe
    to count on" forecast used to size a *commitment* (grid top-up, overnight guarantee) — the
    same risk-aware logic the planner already applies via P10 for commitment sizing. So the
    recommendation should reflect what solar delivers on the disappointing quarter of days, not
    a typical day; sizing off the median would under-charge on the worse half of days.

    Returns None ("not enough data yet") below `_MIN_SLOTS` (48) matched daytime slots (~a few
    days). Otherwise returns:
        recommended_pct: p25 ratio, clamped to [30, 100] and rounded to the nearest 5.
        n_slots: matched daytime slot count the recommendation is based on.
        median_ratio_pct / p25_ratio_pct: the raw actual/p50 ratio percentiles (not clamped or
            rounded).
        current_pct: the value passed in, unchanged.
        delta_pct: recommended_pct − current_pct, or None if current_pct is None.
    """
    matched = _matched_slots(forecast_rows, raw_rows)
    ratios = sorted(
        actual / p50 for actual, _p10, p50, _p90 in matched if p50 >= _MIN_DAYTIME_W
    )
    n = len(ratios)
    if n < _MIN_SLOTS:
        return None

    median_ratio = _percentile(ratios, 0.5)
    p25_ratio = _percentile(ratios, 0.25)

    raw_pct = p25_ratio * 100.0
    clamped_pct = max(30.0, min(100.0, raw_pct))
    recommended_pct = round(clamped_pct / 5.0) * 5.0

    delta_pct = None if current_pct is None else round(recommended_pct - current_pct, 1)

    return {
        "recommended_pct": recommended_pct,
        "n_slots": n,
        "median_ratio_pct": round(median_ratio * 100.0, 1),
        "p25_ratio_pct": round(p25_ratio * 100.0, 1),
        "current_pct": current_pct,
        "delta_pct": delta_pct,
    }
