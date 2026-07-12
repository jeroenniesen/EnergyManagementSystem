"""Forecast/prediction accuracy: how well the system's forward-looking numbers matched what
actually happened, over three independent tracks (B-72):

- `forecast_error` / `recommend_solar_confidence`: the day-ahead solar forecast vs. actual solar.
- `plan_execution_error`: the planner's committed target_soc-by-deadline vs. the SoC actually
  reached — deadline-aware, NOT a naive SoC-vs-target gap at every cycle (a plan's `target_soc` is
  a deadline target, not an instantaneous setpoint, so SoC is legitimately below target while
  still charging toward it, ahead of the deadline; this function only scores the moment the
  deadline arrives, when the gap actually means something).
- `load_baseline_error`: how predictable the household's own load is, against a simple
  trailing-mean day-of-week/hour baseline — the number a future load model (B-64) must beat.

Pure — no clock, no I/O. The export endpoint / `/api/accuracy` hand in stored rows (forecast
snapshots, raw samples, plan history) and these functions score them.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

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


def _parse_local(ts: object, tz) -> datetime | None:
    """ISO timestamp -> aware UTC datetime, matching `ems.retrospect._parse` EXCEPT for the naive
    case: `deadline`/plan-history timestamps are wall-clock-derived (sunset, price peaks), so a
    naive value is interpreted as local time in `tz` rather than assumed already UTC, then
    normalised to UTC so every comparison below happens in one timezone."""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


_DEADLINE_GRACE = timedelta(minutes=30)  # how late a plan_history row may arrive and still count
_MIN_DEADLINES = 3  # measurable deadlines before a rate is trusted


def plan_execution_error(plan_rows: list[dict], *, tz) -> dict | None:
    """How well the planner's committed target_soc-by-deadline actually panned out — the plan
    EXECUTION read (as opposed to `forecast_error`'s plan INPUT read). Each `plan_rows` row is one
    control-cycle snapshot (`ts, strategy, target_soc, deadline, soc_pct, intent`), recorded every
    cycle regardless of whether that cycle set a new target.

    Many consecutive cycles share the same `deadline` while a plan is in force, so deadlines are
    DEDUPED — each unique `deadline` value is scored once, using the LATEST `target_soc` recorded
    for it before the deadline arrives (a later cycle may have revised the target as conditions
    changed, and the final commitment is the one that matters).

    "Achieved" is the `soc_pct` of the first plan_history row at/after the deadline, within 30
    minutes (recorded every cycle, so this is almost always the very next one); a deadline with no
    row landing in that window is not measurable and is skipped — not fabricated.

    Returns None below 3 measurable deadlines (too little evidence). Otherwise:
        n_deadlines: measurable deadline count.
        mean_error_pp: mean(achieved − target) in percentage points, signed — negative means the
            plan under-delivered on average.
        mae_pp: mean(|achieved − target|).
        hit_rate_pct: % of deadlines where achieved >= target − 2pp (a small tolerance for
            last-mile SoC noise/rounding, not a strict miss).
    """
    by_deadline: dict[str, list[dict]] = defaultdict(list)
    for row in plan_rows:
        if row.get("target_soc") is None or row.get("deadline") is None:
            continue
        by_deadline[row["deadline"]].append(row)

    # Every recorded (ts, soc_pct) pair, sorted ascending — the pool "achieved" is matched from.
    timed_soc: list[tuple[datetime, float]] = []
    for row in plan_rows:
        if row.get("soc_pct") is None:
            continue
        dt = _parse_local(row.get("ts"), tz)
        if dt is None:
            continue
        timed_soc.append((dt, float(row["soc_pct"])))
    timed_soc.sort(key=lambda pair: pair[0])

    errors: list[float] = []
    for deadline_str, rows in by_deadline.items():
        deadline_dt = _parse_local(deadline_str, tz)
        if deadline_dt is None:
            continue

        # Latest target recorded BEFORE the deadline (falls back to the latest overall if every
        # row sharing this deadline happens to have ts >= deadline).
        rows_sorted = sorted(rows, key=lambda r: _parse_local(r.get("ts"), tz) or deadline_dt)
        before = [r for r in rows_sorted
                  if (_parse_local(r.get("ts"), tz) or deadline_dt) < deadline_dt]
        target = float((before or rows_sorted)[-1]["target_soc"])

        achieved: float | None = None
        for ts, soc in timed_soc:
            if ts < deadline_dt:
                continue
            if ts - deadline_dt <= _DEADLINE_GRACE:
                achieved = soc
            break  # first row at/after the deadline decides, in or out of the grace window
        if achieved is None:
            continue
        errors.append(achieved - target)

    n = len(errors)
    if n < _MIN_DEADLINES:
        return None
    hits = sum(1 for e in errors if e >= -2.0)
    return {
        "n_deadlines": n,
        "mean_error_pp": round(_mean(errors), 1),
        "mae_pp": round(_mean([abs(e) for e in errors]), 1),
        "hit_rate_pct": round(hits / n * 100.0, 1),
    }


_MIN_PRIOR_DAYS = 3  # prior same-weekday-hour observations required before a bucket is scored
_MIN_LOAD_HOURS = 24  # evaluable hours before the read is trusted


def load_baseline_error(raw_rows: list[dict], *, tz) -> dict | None:
    """How predictable the household's OWN load is, against the simplest defensible baseline: the
    trailing mean of the same day-of-week/hour bucket over prior weeks (e.g. "what did we use on
    previous Mondays at 14:00"). This is the honest number a future load model (B-64) has to beat
    — if a naive weekly-seasonal average already scores well, a fancier model needs to clear that
    bar, not an arbitrary one.

    `raw_rows` (as from `store.raw_between`) are reconstructed into house load per sample
    (`load = grid + solar + battery`, §4) and averaged into local-time (`tz`) hourly buckets.
    For each hourly bucket, the baseline is the trailing (expanding) mean of every STRICTLY EARLIER
    hourly bucket sharing the same (weekday, hour) — at least 3 prior observations are required, or
    that hour is skipped (not enough history yet for that weekday/hour combination). Comparing
    "hour N" against the mean of "hour N's" own history, not the whole raw window, is what makes
    this trailing rather than a single fixed lookup table computed once over everything.

    Returns None below 24 evaluable hours. Otherwise:
        n_hours: evaluable hour count.
        mape_pct: mean absolute percentage error (hours with zero actual load excluded — a
            percentage error is undefined there).
        bias_w: mean(actual − baseline), signed, in watts.
    """
    hourly_samples: dict[tuple, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None:
            continue
        local = dt.astimezone(tz)
        load_w = (
            float(r.get("grid_power_w", 0.0))
            + float(r.get("solar_power_w", 0.0))
            + float(r.get("battery_power_w", 0.0))
        )
        hourly_samples[(local.date(), local.hour)].append(load_w)

    hourly_mean = {key: _mean(vals) for key, vals in hourly_samples.items()}

    by_bucket: dict[tuple, list[tuple]] = defaultdict(list)
    for (day, hour), mean_w in hourly_mean.items():
        by_bucket[(day.weekday(), hour)].append((day, mean_w))
    for entries in by_bucket.values():
        entries.sort(key=lambda pair: pair[0])

    errors: list[float] = []
    pct_errors: list[float] = []
    for entries in by_bucket.values():
        for i, (_day, actual) in enumerate(entries):
            prior = entries[:i]
            if len(prior) < _MIN_PRIOR_DAYS:
                continue
            baseline = _mean([mean_w for _d, mean_w in prior])
            err = actual - baseline
            errors.append(err)
            if actual != 0:
                pct_errors.append(abs(err) / abs(actual) * 100.0)

    n_hours = len(errors)
    if n_hours < _MIN_LOAD_HOURS:
        return None
    return {
        "n_hours": n_hours,
        "mape_pct": round(_mean(pct_errors), 1) if pct_errors else None,
        "bias_w": round(_mean(errors), 1),
    }
