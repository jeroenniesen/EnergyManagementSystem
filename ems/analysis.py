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

from collections import defaultdict

from ems.retrospect import _floor, _mean, _parse

_DH = 15 / 60.0  # hours per 15-min slot


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
    actual_by_slot: dict[object, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None:
            continue
        actual_by_slot[_floor(dt)].append(float(r.get("solar_power_w", 0.0)))

    errors: list[float] = []
    abs_errors: list[float] = []
    in_band = 0
    actual_kwh = 0.0
    forecast_kwh = 0.0
    n_slots = 0
    for row in forecast_rows:
        dt = _parse(row.get("start"))
        if dt is None:
            continue
        slot = _floor(dt)
        samples = actual_by_slot.get(slot)
        if not samples:
            continue  # no actual recorded for this forecast slot — skip, don't fabricate a match
        actual = _mean(samples)
        p50 = float(row.get("p50_w", 0.0))
        p10 = float(row.get("p10_w", 0.0))
        p90 = float(row.get("p90_w", 0.0))

        n_slots += 1
        errors.append(actual - p50)
        abs_errors.append(abs(actual - p50))
        if p10 <= actual <= p90:
            in_band += 1
        actual_kwh += actual * _DH / 1000.0
        forecast_kwh += p50 * _DH / 1000.0

    if n_slots == 0:
        return {
            "n_slots": 0,
            "bias_w": None,
            "mae_w": None,
            "band_coverage_pct": None,
            "actual_solar_kwh": None,
            "forecast_p50_kwh": None,
        }

    return {
        "n_slots": n_slots,
        "bias_w": round(_mean(errors), 1),
        "mae_w": round(_mean(abs_errors), 1),
        "band_coverage_pct": round(in_band / n_slots * 100.0, 1),
        "actual_solar_kwh": round(actual_kwh, 2),
        "forecast_p50_kwh": round(forecast_kwh, 2),
    }
