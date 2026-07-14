"""Forecast/prediction accuracy (pure): solar forecast skill, plan-execution error, and
household load-baseline error, over matched slots/deadlines/hours.

`forecast_error`/`recommend_solar_confidence` consume the prediction ledger's CANONICAL row shape
natively (`target_start, low_w, expected_w, high_w` — see `ems.storage.history.HistoryStore.
ledger_canonical_between`), so `_forecast_row` below builds fixtures in that shape rather than the
legacy `forecast_snapshots` one (`issued_date, start, p10_w, p50_w, p90_w`)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import ems.confidence as confidence
from ems import analysis
from ems.analysis import (
    _legacy_snapshot_row,
    forecast_error,
    load_baseline_error,
    model_health,
    plan_execution_error,
    recommend_solar_confidence,
)

UTC = ZoneInfo("UTC")


def _forecast_row(start: str, low: float, expected: float, high: float) -> dict:
    return {"target_start": start, "low_w": low, "expected_w": expected, "high_w": high}


def _raw_row(ts: str, solar_w: float) -> dict:
    return {"ts": ts, "solar_power_w": solar_w}


def _slot_starts(n: int, *, start="2026-06-01T06:00:00+00:00") -> list[str]:
    t0 = datetime.fromisoformat(start)
    return [(t0 + timedelta(minutes=15 * i)).isoformat() for i in range(n)]


def _daytime_pairs(
    n: int, ratios: list[float], *, p50: float = 1000.0
) -> tuple[list[dict], list[dict]]:
    """n matched daytime slots (p50 >= 200), ratio[i] = actual/p50 for slot i, split across an
    even repeating block per ratio (e.g. ratios=[0.7,0.8,0.9,1.0] with n=48 -> 12 of each)."""
    starts = _slot_starts(n)
    block = n // len(ratios)
    forecasts = [_forecast_row(s, p50 * 0.5, p50, p50 * 1.5) for s in starts]
    raw = [_raw_row(s, ratios[i // block] * p50) for i, s in enumerate(starts)]
    return forecasts, raw


def test_known_slots_bias_mae_and_kwh():
    # 4 slots, forecast p50=1000W, actual=800W throughout -> bias -200, mae 200.
    starts = [
        "2026-06-28T10:00:00+00:00", "2026-06-28T10:15:00+00:00",
        "2026-06-28T10:30:00+00:00", "2026-06-28T10:45:00+00:00",
    ]
    forecasts = [_forecast_row(s, 500.0, 1000.0, 1500.0) for s in starts]
    raw = [_raw_row(s, 800.0) for s in starts]

    out = forecast_error(forecasts, raw)

    assert out["n_slots"] == 4
    assert out["bias_w"] == -200.0
    assert out["mae_w"] == 200.0
    # 4 slots * 800W * 0.25h / 1000 = 0.8 kWh actual; 1000W -> 1.0 kWh forecast.
    assert out["actual_solar_kwh"] == 0.8
    assert out["forecast_p50_kwh"] == 1.0
    assert out["band_coverage_pct"] == 100.0  # 800 is within [500, 1500] every slot


def test_band_coverage_counts_only_slots_inside_p10_p90():
    starts = [
        "2026-06-28T10:00:00+00:00",  # actual inside band
        "2026-06-28T10:15:00+00:00",  # actual outside band (above p90)
    ]
    forecasts = [
        _forecast_row(starts[0], 500.0, 1000.0, 1500.0),
        _forecast_row(starts[1], 500.0, 1000.0, 1500.0),
    ]
    raw = [
        _raw_row(starts[0], 1000.0),  # inside [500, 1500]
        _raw_row(starts[1], 2000.0),  # outside [500, 1500]
    ]

    out = forecast_error(forecasts, raw)

    assert out["n_slots"] == 2
    assert out["band_coverage_pct"] == 50.0


def test_forecast_slot_without_matching_actual_is_skipped():
    forecasts = [
        _forecast_row("2026-06-28T10:00:00+00:00", 500.0, 1000.0, 1500.0),
        _forecast_row("2026-06-28T11:00:00+00:00", 500.0, 1000.0, 1500.0),  # no actual for this one
    ]
    raw = [_raw_row("2026-06-28T10:05:00+00:00", 900.0)]  # only covers the 10:00 slot

    out = forecast_error(forecasts, raw)

    assert out["n_slots"] == 1
    assert out["bias_w"] == -100.0  # 900 - 1000


def test_empty_input_returns_zero_slots_without_crashing():
    out = forecast_error([], [])
    assert out["n_slots"] == 0
    assert out["bias_w"] is None
    assert out["mae_w"] is None
    assert out["band_coverage_pct"] is None
    assert out["actual_solar_kwh"] is None
    assert out["forecast_p50_kwh"] is None


def test_no_overlap_between_forecast_and_raw_windows_is_zero_slots():
    forecasts = [_forecast_row("2026-06-28T10:00:00+00:00", 500.0, 1000.0, 1500.0)]
    raw = [_raw_row("2026-06-29T10:00:00+00:00", 900.0)]  # a different day entirely
    out = forecast_error(forecasts, raw)
    assert out["n_slots"] == 0


def test_legacy_snapshot_row_maps_to_ledger_native_shape():
    # _legacy_snapshot_row exists so a legacy forecast_snapshots-shaped row (retained as a
    # read-only archive/migration-source table, no longer written by the recorder) can still be
    # scored through forecast_error/recommend_solar_confidence without duplicating the mapping.
    legacy = {"issued_date": "2026-06-28", "start": "2026-06-28T10:00:00+00:00",
              "p10_w": 500.0, "p50_w": 1000.0, "p90_w": 1500.0}
    assert _legacy_snapshot_row(legacy) == {
        "target_start": "2026-06-28T10:00:00+00:00",
        "low_w": 500.0, "expected_w": 1000.0, "high_w": 1500.0,
    }
    # And the mapped shape actually scores identically to a native ledger row.
    native = _forecast_row("2026-06-28T10:00:00+00:00", 500.0, 1000.0, 1500.0)
    raw = [_raw_row("2026-06-28T10:00:00+00:00", 900.0)]
    assert forecast_error([_legacy_snapshot_row(legacy)], raw) == forecast_error([native], raw)


# ---- recommend_solar_confidence: evidence-based advisory recommendation for the settings knob ----

def test_recommend_known_ratios_gives_exact_p25_median_and_recommendation():
    # 48 slots, 12 each of ratio 0.7/0.8/0.9/1.0 (sorted already since generated in blocks).
    # Nearest-rank p25 = the 12th smallest (index 11) = 0.7; p50 = the 24th smallest (idx 23) = 0.8.
    forecasts, raw = _daytime_pairs(48, [0.7, 0.8, 0.9, 1.0])

    out = recommend_solar_confidence(forecasts, raw, current_pct=80.0)

    assert out["n_slots"] == 48
    assert out["p25_ratio_pct"] == 70.0
    assert out["median_ratio_pct"] == 80.0
    assert out["recommended_pct"] == 70.0  # p25 (70.0), already in [30,100] and a multiple of 5
    assert out["current_pct"] == 80.0
    assert out["delta_pct"] == -10.0


def test_recommend_fewer_than_48_matched_daytime_slots_returns_none():
    forecasts, raw = _daytime_pairs(47, [0.8])  # one below the 48-slot threshold
    assert recommend_solar_confidence(forecasts, raw) is None


def test_recommend_exactly_48_slots_is_the_minimum_that_works():
    forecasts, raw = _daytime_pairs(48, [0.8])
    assert recommend_solar_confidence(forecasts, raw) is not None


def test_recommend_excludes_low_light_slots_from_ratio_and_count():
    # 48 real daytime slots (p50=1000W) driving the recommendation, plus 5 low-light slots
    # (p50=100W < 200W floor) with a wildly different ratio that must NOT move the result.
    forecasts, raw = _daytime_pairs(48, [0.7, 0.8, 0.9, 1.0])
    dusk_starts = _slot_starts(5, start="2026-06-01T20:00:00+00:00")
    forecasts = forecasts + [_forecast_row(s, 50.0, 100.0, 150.0) for s in dusk_starts]
    raw = raw + [_raw_row(s, 1000.0) for s in dusk_starts]  # ratio 10x — would skew p25 badly

    out = recommend_solar_confidence(forecasts, raw)

    assert out["n_slots"] == 48  # the 5 dusk slots are excluded, not just down-weighted
    assert out["p25_ratio_pct"] == 70.0
    assert out["median_ratio_pct"] == 80.0


def test_recommend_clamps_low_ratio_to_the_30pct_floor():
    forecasts, raw = _daytime_pairs(48, [0.2])  # p25 ratio -> 20%, below the 30% floor
    out = recommend_solar_confidence(forecasts, raw)
    assert out["p25_ratio_pct"] == 20.0
    assert out["recommended_pct"] == 30.0


def test_recommend_clamps_high_ratio_to_the_100pct_ceiling():
    forecasts, raw = _daytime_pairs(48, [1.5])  # p25 ratio -> 150%, above the 100% ceiling
    out = recommend_solar_confidence(forecasts, raw)
    assert out["p25_ratio_pct"] == 150.0
    assert out["recommended_pct"] == 100.0


def test_recommend_rounds_to_nearest_5pct():
    forecasts, raw = _daytime_pairs(48, [0.63])  # 63% -> rounds to 65%, not the raw 63%
    out = recommend_solar_confidence(forecasts, raw)
    assert out["recommended_pct"] == 65.0


def test_recommend_delta_is_none_without_a_current_value():
    forecasts, raw = _daytime_pairs(48, [0.8])
    out = recommend_solar_confidence(forecasts, raw)
    assert out["current_pct"] is None
    assert out["delta_pct"] is None


def test_recommend_empty_input_returns_none():
    assert recommend_solar_confidence([], []) is None


# ---- plan_execution_error: deadline-aware target_soc-vs-achieved-SoC scoring ----

def _plan_row(ts: str, *, target: float | None = None, deadline: str | None = None,
              soc: float | None = None) -> dict:
    return {"ts": ts, "strategy": "winter", "target_soc": target, "deadline": deadline,
            "soc_pct": soc, "intent": "grid_charge_to_target"}


def test_plan_execution_error_hand_computed_across_three_deadlines():
    # 3 unique deadlines (one per day). Deadline 1 has its target REVISED mid-flight (70 -> 75) —
    # dedup must use the latest (75), not the first. Achieved is read from the next plan_history
    # row after each deadline (soc_pct only, no target/deadline on that row — a normal cycle).
    rows = [
        _plan_row("2026-06-01T17:00:00+00:00", target=70.0,
                  deadline="2026-06-01T18:00:00+00:00", soc=60.0),
        _plan_row("2026-06-01T17:30:00+00:00", target=75.0,
                  deadline="2026-06-01T18:00:00+00:00", soc=68.0),
        _plan_row("2026-06-01T18:05:00+00:00", soc=80.0),  # achieved for deadline 1: 80 - 75 = 5
        _plan_row("2026-06-02T17:00:00+00:00", target=80.0,
                  deadline="2026-06-02T18:00:00+00:00", soc=72.0),
        _plan_row("2026-06-02T18:10:00+00:00", soc=76.0),  # achieved for deadline 2: 76 - 80 = -4
        _plan_row("2026-06-03T17:00:00+00:00", target=90.0,
                  deadline="2026-06-03T18:00:00+00:00", soc=82.0),
        _plan_row("2026-06-03T18:00:00+00:00", soc=88.0),  # achieved exactly AT deadline: -2
    ]
    out = plan_execution_error(rows, tz=UTC)
    assert out is not None
    assert out["n_deadlines"] == 3
    # errors = [5, -4, -2] -> mean -1/3, mae 11/3
    assert out["mean_error_pp"] == -0.3
    assert out["mae_pp"] == 3.7
    # hit = achieved >= target - 2pp: 5 hits, -4 misses, -2 hits (boundary, inclusive) -> 2/3
    assert out["hit_rate_pct"] == 66.7


def test_plan_execution_error_dedupes_shared_deadline_using_latest_target():
    # Deadline 1 is recorded across THREE cycles as the target is progressively revised
    # (70 -> 72 -> 75). Using the latest (75) against achieved=80 gives error 5; using the first
    # (70) would wrongly give 10 and change every aggregate below.
    rows = [
        _plan_row("2026-06-10T17:00:00+00:00", target=70.0,
                  deadline="2026-06-10T18:00:00+00:00", soc=60.0),
        _plan_row("2026-06-10T17:20:00+00:00", target=72.0,
                  deadline="2026-06-10T18:00:00+00:00", soc=65.0),
        _plan_row("2026-06-10T17:40:00+00:00", target=75.0,
                  deadline="2026-06-10T18:00:00+00:00", soc=70.0),
        _plan_row("2026-06-10T18:05:00+00:00", soc=80.0),  # error = 80 - 75 = 5
        _plan_row("2026-06-11T17:00:00+00:00", target=50.0,
                  deadline="2026-06-11T18:00:00+00:00", soc=45.0),
        _plan_row("2026-06-11T18:00:00+00:00", soc=50.0),  # error = 0
        _plan_row("2026-06-12T17:00:00+00:00", target=60.0,
                  deadline="2026-06-12T18:00:00+00:00", soc=55.0),
        _plan_row("2026-06-12T18:00:00+00:00", soc=60.0),  # error = 0
    ]
    out = plan_execution_error(rows, tz=UTC)
    assert out["n_deadlines"] == 3
    # errors = [5, 0, 0] -> mean/mae = 5/3 = 1.6667 -> 1.7
    assert out["mean_error_pp"] == 1.7
    assert out["mae_pp"] == 1.7
    assert out["hit_rate_pct"] == 100.0


def test_plan_execution_error_achieved_row_exactly_30min_late_counts():
    # One deadline's achieved row lands exactly at the 30-min grace boundary (inclusive) and one
    # exactly at the deadline; a third has a genuine -2pp miss right at the hit-rate boundary.
    rows = [
        _plan_row("2026-06-20T17:00:00+00:00", target=70.0,
                  deadline="2026-06-20T18:00:00+00:00", soc=60.0),
        _plan_row("2026-06-20T18:30:00+00:00", soc=75.0),  # +30 min exactly -> counts, error 5
        _plan_row("2026-06-21T17:00:00+00:00", target=70.0,
                  deadline="2026-06-21T18:00:00+00:00", soc=60.0),
        _plan_row("2026-06-21T18:15:00+00:00", soc=68.0),  # error -2 -> hit-rate boundary (hit)
        _plan_row("2026-06-22T17:00:00+00:00", target=70.0,
                  deadline="2026-06-22T18:00:00+00:00", soc=60.0),
        _plan_row("2026-06-22T18:00:00+00:00", soc=75.0),  # error 5
    ]
    out = plan_execution_error(rows, tz=UTC)
    assert out["n_deadlines"] == 3
    assert out["mean_error_pp"] == 2.7  # (5 - 2 + 5) / 3
    assert out["mae_pp"] == 4.0         # (5 + 2 + 5) / 3
    assert out["hit_rate_pct"] == 100.0  # -2 counts as a hit (achieved >= target - 2pp)


def test_plan_execution_error_achieved_row_31min_late_is_not_measurable():
    # Same shape as the 30-min case, but this deadline's only later row is 31 minutes out — one
    # minute past the grace window — so it must NOT be counted, dropping this run below the
    # 3-measurable-deadlines minimum (only 2 of the 3 deadlines below are measurable).
    rows = [
        _plan_row("2026-06-20T17:00:00+00:00", target=70.0,
                  deadline="2026-06-20T18:00:00+00:00", soc=60.0),
        _plan_row("2026-06-20T18:30:00+00:00", soc=75.0),
        _plan_row("2026-06-21T17:00:00+00:00", target=70.0,
                  deadline="2026-06-21T18:00:00+00:00", soc=60.0),
        _plan_row("2026-06-21T18:31:00+00:00", soc=75.0),  # 31 min late -> not measurable
        _plan_row("2026-06-22T17:00:00+00:00", target=70.0,
                  deadline="2026-06-22T18:00:00+00:00", soc=60.0),
        _plan_row("2026-06-22T18:00:00+00:00", soc=75.0),
    ]
    assert plan_execution_error(rows, tz=UTC) is None


def test_plan_execution_error_fewer_than_three_measurable_deadlines_is_none():
    rows = [
        _plan_row("2026-06-01T17:00:00+00:00", target=70.0,
                  deadline="2026-06-01T18:00:00+00:00", soc=60.0),
        _plan_row("2026-06-01T18:05:00+00:00", soc=80.0),
        _plan_row("2026-06-02T17:00:00+00:00", target=80.0,
                  deadline="2026-06-02T18:00:00+00:00", soc=72.0),
        _plan_row("2026-06-02T18:10:00+00:00", soc=76.0),
    ]
    assert plan_execution_error(rows, tz=UTC) is None


def test_plan_execution_error_empty_input_is_none():
    assert plan_execution_error([], tz=UTC) is None


def test_plan_execution_error_ignores_rows_missing_target_or_deadline():
    # Rows with only one of target_soc/deadline set (a partial/older snapshot) contribute nothing.
    rows = [
        _plan_row("2026-06-01T17:00:00+00:00", target=70.0, soc=60.0),  # no deadline
        _plan_row("2026-06-01T18:00:00+00:00", deadline="2026-06-01T18:00:00+00:00", soc=60.0),
    ]
    assert plan_execution_error(rows, tz=UTC) is None


# ---- load_baseline_error: household load vs. a trailing day-of-week/hour baseline ----

def _load_row(ts: str, grid_w: float) -> dict:
    return {"ts": ts, "grid_power_w": grid_w, "solar_power_w": 0.0, "battery_power_w": 0.0}


def _weekly_rows(hours: list[int], n_weeks: int, *, anomaly_w: float | None = None,
                  base_w: float = 1000.0, anchor: str = "2026-06-01T00:00:00+00:00") -> list[dict]:
    """`len(hours)` buckets, each sampled once/week for `n_weeks` weeks (same weekday every time,
    since each step is exactly 7 days). Every occurrence is `base_w` except the LAST occurrence of
    each bucket, which is `anomaly_w` (if given) — makes the trailing-mean baseline hand-computable
    (every prior observation is identical, so the baseline is exactly `base_w` until the anomaly).
    """
    t0 = datetime.fromisoformat(anchor)
    rows = []
    for h in hours:
        for week in range(n_weeks):
            ts = t0 + timedelta(days=7 * week, hours=h)
            w = base_w
            if anomaly_w is not None and week == n_weeks - 1:
                w = anomaly_w
            rows.append(_load_row(ts.isoformat(), w))
    return rows


def test_load_baseline_error_hand_computed_bias_and_mape():
    # 4 hour-buckets x 10 weekly occurrences; every occurrence is 1000W except the LAST of each
    # bucket (1200W). Occurrences need >= 3 PRIOR same-bucket weeks, so index 0/1/2 (of 10) are
    # not evaluable -> 7 evaluable per bucket (indices 3..9), 28 total; 24 of those are the
    # 1000-vs-1000 baseline (error 0) and 4 are the final 1200-vs-1000 anomaly (error 200).
    rows = _weekly_rows([6, 10, 14, 18], 10, anomaly_w=1200.0)
    out = load_baseline_error(rows, tz=UTC)
    assert out is not None
    assert out["n_hours"] == 28
    assert out["bias_w"] == 28.6      # mean of 24 zeros + 4 * 200, over 28 -> 800/28
    assert out["mape_pct"] == 2.4     # mean of 24 zeros + 4 * (200/1200*100), over 28


def test_load_baseline_error_skips_buckets_with_fewer_than_three_prior_days():
    # Exactly 4 weekly occurrences per bucket: only the 4th (index 3) has the required 3 PRIOR
    # weeks, so only 1-in-4 is evaluable per bucket. 24 buckets (hours 0..23) x 1 evaluable each =
    # exactly the 24-hour minimum; every value is identical (500W) so error is exactly zero.
    rows = _weekly_rows(list(range(24)), 4, base_w=500.0)
    out = load_baseline_error(rows, tz=UTC)
    assert out is not None
    assert out["n_hours"] == 24
    assert out["bias_w"] == 0.0
    assert out["mape_pct"] == 0.0


def test_load_baseline_error_never_reaching_three_prior_days_is_none():
    # Only 3 weekly occurrences per bucket: index 2 (the latest) would need 2 priors < 3 -> the
    # minimum is NEVER reached for any bucket, however many hours/weeks of data exist.
    rows = _weekly_rows(list(range(24)), 3, base_w=500.0)
    assert load_baseline_error(rows, tz=UTC) is None


def test_load_baseline_error_below_24_evaluable_hours_is_none():
    # 20 buckets (not 24) with 4 occurrences each -> only 20 evaluable hours, below the minimum.
    rows = _weekly_rows(list(range(20)), 4, base_w=500.0)
    assert load_baseline_error(rows, tz=UTC) is None


def test_load_baseline_error_empty_input_is_none():
    assert load_baseline_error([], tz=UTC) is None


# ---- model_health (B-76): synthesized ok/warn/unknown verdict per track, no new measurement ----

# A solar-skill dict with plenty of evidence and a well-calibrated forecast — mirrors
# test_confidence.py's _GOOD_SKILL: mean p50 = 42 kWh * 1000 / (200 * 0.25h) = 840 W.
_GOOD_SOLAR = {
    "n_slots": 200, "bias_w": 10.0, "mae_w": 50.0, "band_coverage_pct": 92.0,
    "actual_solar_kwh": 40.0, "forecast_p50_kwh": 42.0,
}
_GOOD_LOAD = {"n_hours": 100, "mape_pct": 10.0, "bias_w": 5.0}
_GOOD_PLAN = {"n_deadlines": 10, "mean_error_pp": 0.5, "mae_pp": 1.0, "hit_rate_pct": 90.0}


def test_model_health_imports_not_duplicates_confidence_constants():
    # The task is explicit: mirror confidence.py's thresholds by IMPORTING them, never re-deriving
    # the same 25%/60% numbers a second time — importing binds the identical object, so `is` holds.
    assert analysis._MAX_BIAS_FRACTION is confidence._MAX_BIAS_FRACTION
    assert analysis._MIN_BAND_COVERAGE_PCT is confidence._MIN_BAND_COVERAGE_PCT
    assert analysis._MIN_SKILL_SLOTS is confidence._MIN_SKILL_SLOTS


def test_model_health_everything_fine_is_all_ok_with_no_notes():
    out = model_health(solar=_GOOD_SOLAR, load=_GOOD_LOAD, plan_execution=_GOOD_PLAN)
    assert out == {"solar": "ok", "load": "ok", "plan_execution": "ok", "notes": []}


def test_model_health_all_none_tracks_are_unknown_not_alarming():
    # The honest fresh-install empty state (item 3): no evidence yet must never read as ok or warn.
    out = model_health(solar=None, load=None, plan_execution=None)
    assert out == {"solar": "unknown", "load": "unknown", "plan_execution": "unknown", "notes": []}


def test_model_health_zero_evidence_solar_dict_is_unknown_not_ok():
    # forecast_error() always returns a dict (never None) even with zero matched slots — that must
    # still read 'unknown', not a falsely-confident 'ok'.
    zero = {"n_slots": 0, "bias_w": None, "mae_w": None, "band_coverage_pct": None,
            "actual_solar_kwh": None, "forecast_p50_kwh": None}
    out = model_health(solar=zero, load=_GOOD_LOAD, plan_execution=_GOOD_PLAN)
    assert out["solar"] == "unknown"


def test_model_health_thin_solar_evidence_below_min_skill_slots_is_unknown():
    thin = {**_GOOD_SOLAR, "n_slots": confidence._MIN_SKILL_SLOTS - 1}
    out = model_health(solar=thin, load=_GOOD_LOAD, plan_execution=_GOOD_PLAN)
    assert out["solar"] == "unknown"


def test_model_health_solar_bias_beyond_threshold_warns_with_a_note():
    # 300 W bias vs. 840 W mean p50 exceeds the 25% threshold (210 W) — same fixture as
    # test_confidence.py's equivalent case.
    hot = {**_GOOD_SOLAR, "bias_w": 300.0}
    out = model_health(solar=hot, load=_GOOD_LOAD, plan_execution=_GOOD_PLAN)
    assert out["solar"] == "warn"
    assert len(out["notes"]) == 1
    assert "solar forecast" in out["notes"][0].lower()


def test_model_health_solar_bias_exactly_at_threshold_is_not_warn():
    # Strict '>' (mirrors confidence.py's _forecast_bias_flag) — exactly 25% of mean p50 is fine.
    at_threshold = {**_GOOD_SOLAR, "bias_w": 210.0}
    out = model_health(solar=at_threshold, load=_GOOD_LOAD, plan_execution=_GOOD_PLAN)
    assert out["solar"] == "ok"


def test_model_health_thin_band_coverage_warns_even_with_low_bias():
    thin_band = {**_GOOD_SOLAR, "band_coverage_pct": 40.0}
    out = model_health(solar=thin_band, load=_GOOD_LOAD, plan_execution=_GOOD_PLAN)
    assert out["solar"] == "warn"


def test_model_health_band_coverage_exactly_at_threshold_is_not_warn():
    at_threshold = {**_GOOD_SOLAR, "band_coverage_pct": 60.0}  # strict '<' — 60 itself is fine
    out = model_health(solar=at_threshold, load=_GOOD_LOAD, plan_execution=_GOOD_PLAN)
    assert out["solar"] == "ok"


def test_model_health_load_none_is_unknown():
    out = model_health(solar=_GOOD_SOLAR, load=None, plan_execution=_GOOD_PLAN)
    assert out["load"] == "unknown"


def test_model_health_load_dict_with_no_mape_is_unknown():
    # Rare edge case in load_baseline_error: every evaluable hour had zero actual load, so
    # mape_pct is None even though the dict itself isn't.
    out = model_health(
        solar=_GOOD_SOLAR, load={"n_hours": 30, "mape_pct": None, "bias_w": 0.0},
        plan_execution=_GOOD_PLAN,
    )
    assert out["load"] == "unknown"


def test_model_health_load_mape_above_40pct_warns_with_a_note():
    out = model_health(
        solar=_GOOD_SOLAR, load={**_GOOD_LOAD, "mape_pct": 40.1}, plan_execution=_GOOD_PLAN,
    )
    assert out["load"] == "warn"
    assert any("load" in n.lower() for n in out["notes"])


def test_model_health_load_mape_exactly_40pct_is_not_warn():
    out = model_health(
        solar=_GOOD_SOLAR, load={**_GOOD_LOAD, "mape_pct": 40.0}, plan_execution=_GOOD_PLAN,
    )
    assert out["load"] == "ok"


def test_model_health_plan_execution_none_is_unknown():
    out = model_health(solar=_GOOD_SOLAR, load=_GOOD_LOAD, plan_execution=None)
    assert out["plan_execution"] == "unknown"


def test_model_health_plan_execution_hit_rate_below_70pct_warns_with_a_note():
    out = model_health(
        solar=_GOOD_SOLAR, load=_GOOD_LOAD,
        plan_execution={**_GOOD_PLAN, "hit_rate_pct": 69.9},
    )
    assert out["plan_execution"] == "warn"
    assert any("plan" in n.lower() for n in out["notes"])


def test_model_health_plan_execution_hit_rate_exactly_70pct_is_not_warn():
    out = model_health(
        solar=_GOOD_SOLAR, load=_GOOD_LOAD,
        plan_execution={**_GOOD_PLAN, "hit_rate_pct": 70.0},
    )
    assert out["plan_execution"] == "ok"


def test_model_health_notes_are_ordered_solar_load_plan_execution():
    out = model_health(
        solar={**_GOOD_SOLAR, "bias_w": 300.0},
        load={**_GOOD_LOAD, "mape_pct": 50.0},
        plan_execution={**_GOOD_PLAN, "hit_rate_pct": 50.0},
    )
    assert out["solar"] == out["load"] == out["plan_execution"] == "warn"
    assert len(out["notes"]) == 3
    assert "solar" in out["notes"][0].lower()
    assert "load" in out["notes"][1].lower()
    assert "plan" in out["notes"][2].lower()
