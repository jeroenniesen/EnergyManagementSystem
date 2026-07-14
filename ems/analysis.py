"""Forecast/prediction accuracy: how well the system's forward-looking numbers matched what
actually happened, over three independent tracks (B-72):

- `forecast_error` / `recommend_solar_confidence`: the day-ahead solar forecast vs. actual solar.
  Both consume the CANONICAL rows of the prediction ledger (design doc §4.2/§4.3,
  `ems.storage.history.HistoryStore.ledger_canonical_between`) — the single scoring source every
  solar-accuracy surface reads (System page, `/api/accuracy`, the solar-confidence advisor, the
  export package). Nowcasts and other same-day rows are never scored, only the 18:00 day-ahead
  canonical snapshot — see `forecast_error`'s docstring for why.
- `plan_execution_error`: the planner's committed target_soc-by-deadline vs. the SoC actually
  reached — deadline-aware, NOT a naive SoC-vs-target gap at every cycle (a plan's `target_soc` is
  a deadline target, not an instantaneous setpoint, so SoC is legitimately below target while
  still charging toward it, ahead of the deadline; this function only scores the moment the
  deadline arrives, when the gap actually means something).
- `load_baseline_error`: how predictable the household's own load is, against a simple
  trailing-mean day-of-week/hour baseline — the number a future load model (B-64) must beat.
- `model_health` (B-76): a synthesized ok/warn/unknown verdict per track — SYNTHESIS ONLY, no new
  measurement — for the System page's "Model health" panel. Reuses the exact same evidence-gate and
  bias/band-coverage rule `ems.confidence.plan_confidence` already applies to the solar track (the
  constants and the rule function are imported from there, not duplicated), so the panel and the
  plan-confidence chip never disagree about what "the forecast is running hot/cold" means.

Pure — no clock, no I/O. The export endpoint / `/api/accuracy` hand in stored rows (ledger
canonical rows, raw samples, plan history) and these functions score them.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from ems.confidence import _MAX_BIAS_FRACTION, _MIN_BAND_COVERAGE_PCT, _MIN_SKILL_SLOTS
from ems.confidence import _forecast_bias_flag as _solar_bias_or_band_flag
from ems.retrospect import _floor, _mean, _parse

_DH = 15 / 60.0  # hours per 15-min slot


def _matched_slots(
    forecast_rows: list[dict], raw_rows: list[dict]
) -> list[tuple[float, float, float, float]]:
    """Bucket actual solar (raw_rows) into 15-min slots and pair each ledger CANONICAL solar row
    (`target_start, low_w, expected_w, high_w` — the shape returned by
    `ems.storage.history.HistoryStore.ledger_canonical_between('solar', ...)`) with its matched
    actual — the shared bucketing behind both `forecast_error` and `recommend_solar_confidence`,
    so they score the exact same slots.

    Returns a list of `(actual_w, low_w, expected_w, high_w)` tuples, one per slot where both a
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
        dt = _parse(row.get("target_start"))
        if dt is None:
            continue
        samples = actual_by_slot.get(_floor(dt))
        if not samples:
            continue  # no actual recorded for this forecast slot — skip, don't fabricate a match
        actual = _mean(samples)
        expected = float(row.get("expected_w", 0.0))
        low = float(row.get("low_w", 0.0))
        high = float(row.get("high_w", 0.0))
        matched.append((actual, low, expected, high))
    return matched


def _legacy_snapshot_row(row: dict) -> dict:
    """Map a legacy `forecast_snapshots`-shaped row (`issued_date, start, p10_w, p50_w, p90_w` —
    the table `ems.storage.history` retains as an archive/migration source, no longer written by
    the recorder) to the ledger-native shape `forecast_error`/`recommend_solar_confidence` consume
    (`target_start, low_w, expected_w, high_w`). Nothing in the live gather path calls this —
    every reader now queries `ledger_canonical_between` directly — it exists only so a
    legacy-shaped caller (e.g. a one-off script reading the retained `forecast_snapshots` table
    straight, design §4.5) can still score through the same two functions without duplicating the
    field mapping."""
    return {
        "target_start": row.get("start"),
        "low_w": row.get("p10_w"),
        "expected_w": row.get("p50_w"),
        "high_w": row.get("p90_w"),
    }


def forecast_error(forecast_rows: list[dict], raw_rows: list[dict]) -> dict:
    """Bucket actual solar (raw_rows) into 15-min slots and compare against the CANONICAL
    day-ahead ledger rows for solar (forecast_rows, each `target_start, low_w, expected_w,
    high_w` — see `ems.storage.history.HistoryStore.ledger_canonical_between('solar', ...)`) over
    the slots where both exist.

    Scoring is restricted to canonical rows on purpose (design doc §3.3/§4.3): the prediction
    ledger's canonical (18:00 local, day-ahead) snapshot is the single scoring source every
    solar-accuracy surface reads — a same-day nowcast is easier to get right than a genuine
    day-ahead commitment, so scoring it too would make the forecast look better than the number
    the planner actually acted on. This intentionally EXCLUDES nowcasts and other same-day rows
    even though they remain in the ledger for lead-time diagnostics — the reported bias/MAE/
    coverage numbers are therefore slightly WORSE than the old date-keyed, same-day-inclusive
    read, but honest, and the same for every consumer (no more contradictory "solar accuracy"
    figures across the UI).

    Returns:
        n_slots: matched slot count (0 if forecast and actuals never overlap).
        bias_w: mean(actual − expected) — negative means the forecast over-predicts solar.
        mae_w: mean(|actual − expected|).
        band_coverage_pct: % of matched slots where low <= actual <= high.
        actual_solar_kwh / forecast_p50_kwh: energy over the matched slots only (the `p50` in
            `forecast_p50_kwh`'s name is historical — the value is the canonical `expected_w`
            energy; the key is unchanged so existing consumers, e.g. `ems.confidence`, need no
            change).
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
            "n_daytime_slots": 0,
            "daytime_bias_w": None,
            "daytime_band_coverage_pct": None,
        }

    errors = [actual - expected for actual, _low, expected, _high in matched]
    abs_errors = [abs(e) for e in errors]
    in_band = sum(1 for actual, low, _expected, high in matched if low <= actual <= high)
    actual_kwh = sum(actual * _DH / 1000.0 for actual, _low, _expected, _high in matched)
    forecast_kwh = sum(expected * _DH / 1000.0 for _actual, _low, expected, _high in matched)

    daytime = [m for m in matched if m[2] >= _MIN_DAYTIME_W]
    daytime_errors = [a - expected for a, _low, expected, _high in daytime]
    daytime_in_band = sum(1 for a, low, _expected, high in daytime if low <= a <= high)
    return {
        "n_slots": n_slots,
        "bias_w": round(_mean(errors), 1),
        "mae_w": round(_mean(abs_errors), 1),
        "band_coverage_pct": round(in_band / n_slots * 100.0, 1),
        "actual_solar_kwh": round(actual_kwh, 2),
        "forecast_p50_kwh": round(forecast_kwh, 2),
        "n_daytime_slots": len(daytime),
        "daytime_bias_w": round(_mean(daytime_errors), 1) if daytime else None,
        "daytime_band_coverage_pct": (round(daytime_in_band / len(daytime) * 100.0, 1)
                                      if daytime else None),
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
    """Recommend a value for `planner.solar_confidence` from logged CANONICAL day-ahead forecast
    performance (`forecast_rows` — see `forecast_error`'s docstring for the row shape and why
    nowcasts are excluded from scoring), over daytime slots (`expected_w >= 200 W`) matched the
    same way as `forecast_error`.

    Why the 25th percentile (not the mean/median): `solar_confidence` scales the expected forecast
    down to a "safe to count on" forecast used to size a *commitment* (grid top-up, overnight
    guarantee) — the same risk-aware logic the planner already applies via the low band for
    commitment sizing. So the recommendation should reflect what solar delivers on the
    disappointing quarter of days, not a typical day; sizing off the median would under-charge on
    the worse half of days.

    Returns None ("not enough data yet") below `_MIN_SLOTS` (48) matched daytime slots (~a few
    days). Otherwise returns:
        recommended_pct: p25 ratio, clamped to [30, 100] and rounded to the nearest 5.
        n_slots: matched daytime slot count the recommendation is based on.
        median_ratio_pct / p25_ratio_pct: the raw actual/expected ratio percentiles (not clamped
            or rounded).
        current_pct: the value passed in, unchanged.
        delta_pct: recommended_pct − current_pct, or None if current_pct is None.
    """
    matched = _matched_slots(forecast_rows, raw_rows)
    ratios = sorted(
        actual / expected for actual, _low, expected, _high in matched
        if expected >= _MIN_DAYTIME_W
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


# B-76 "Model and optimization health": the load/plan_execution warn thresholds are new (no sibling
# constant elsewhere to mirror); the solar rule reuses confidence.py's constants/function verbatim
# (see the imports above) instead of re-deriving the same 25%/60% numbers a second time.
_LOAD_MAPE_WARN_PCT = 40.0  # household load MAPE beyond this reads as "harder to predict lately"
_PLAN_HIT_RATE_WARN_PCT = 70.0  # deadline hit-rate below this reads as "missing targets lately"


def _solar_health(solar: dict | None, *, daytime_only: bool = False) -> tuple[str, str | None]:
    """`solar` is `forecast_error()`'s return shape (always a dict, never None, once there's a
    store at all — see `/api/accuracy`). Below `_MIN_SKILL_SLOTS` matched daytime slots there simply
    isn't enough evidence to call it either way — same evidence gate `plan_confidence` already uses
    for its own "still learning your roof" reason — so this reads 'unknown', not a falsely-confident
    'ok'."""
    # Model health is deliberately daytime-only; legacy payloads without daytime evidence are
    # unknown rather than silently falling back to night-inclusive counts.
    if (
        solar is None
        or (daytime_only and "n_daytime_slots" not in solar)
        or ((solar.get("n_daytime_slots") if daytime_only else solar.get("n_slots")) or 0)
        < _MIN_SKILL_SLOTS
    ):
        return "unknown", None
    daytime = dict(solar)
    if daytime_only:
        daytime["n_slots"] = daytime["n_daytime_slots"]
        daytime["bias_w"] = daytime.get("daytime_bias_w")
        daytime["band_coverage_pct"] = daytime.get("daytime_band_coverage_pct")
    if _solar_bias_or_band_flag(daytime):
        return "warn", (
            f"Solar forecast bias is beyond {_MAX_BIAS_FRACTION * 100:.0f}% of typical output, "
            f"or fewer than {_MIN_BAND_COVERAGE_PCT:.0f}% of readings landed inside its forecast "
            "band, over the last 14 days."
        )
    return "ok", None


def _load_health(load: dict | None) -> tuple[str, str | None]:
    """`load` is `load_baseline_error()`'s return — None below its own evidence minimum (24
    evaluable hours), or (rarely) a dict with `mape_pct` still None (every evaluable hour had zero
    actual load) — both read as 'unknown', never a fabricated 'ok'."""
    mape = None if load is None else load.get("mape_pct")
    if mape is None:
        return "unknown", None
    if mape > _LOAD_MAPE_WARN_PCT:
        return "warn", (
            "Household load has been harder to predict than a simple weekly baseline lately."
        )
    return "ok", None


def _plan_execution_health(plan_execution: dict | None) -> tuple[str, str | None]:
    """`plan_execution` is `plan_execution_error()`'s return — None below its own evidence minimum
    (3 measurable deadlines)."""
    hit_rate = None if plan_execution is None else plan_execution.get("hit_rate_pct")
    if hit_rate is None:
        return "unknown", None
    if hit_rate < _PLAN_HIT_RATE_WARN_PCT:
        return "warn", "The plan has been missing its SoC-by-deadline targets more than expected."
    return "ok", None


def model_health(
    *, solar: dict | None, load: dict | None, plan_execution: dict | None,
    daytime_only: bool = False,
) -> dict:
    """Synthesize an ok/warn/unknown verdict per accuracy track (BACKLOG B-76) — SYNTHESIS ONLY,
    no new measurement is taken here; the three inputs are exactly `/api/accuracy`'s `solar` /
    `load` / `plan_execution` values. Powers the System page's "Model health" panel: whether EMS is
    predicting and executing well enough to trust, at a glance.

    Returns `{"solar": ..., "load": ..., "plan_execution": ..., "notes": [str, ...]}` — each of the
    first three is "ok" | "warn" | "unknown" (never anything else); `notes` holds one plain-language
    sentence per warn row, in solar/load/plan_execution order (never for "unknown" — that state is
    its own honest, non-alarming "still collecting evidence" story, not a note).
    """
    solar_status, solar_note = _solar_health(solar, daytime_only=daytime_only)
    load_status, load_note = _load_health(load)
    plan_status, plan_note = _plan_execution_health(plan_execution)
    return {
        "solar": solar_status,
        "load": load_status,
        "plan_execution": plan_status,
        "notes": [n for n in (solar_note, load_note, plan_note) if n],
    }
