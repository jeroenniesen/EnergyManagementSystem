"""Forecast skill (pure): actual-vs-forecast solar error over matched 15-min slots."""
from datetime import datetime, timedelta

from ems.analysis import forecast_error, recommend_solar_confidence


def _forecast_row(start: str, p10: float, p50: float, p90: float) -> dict:
    return {"issued_date": start[:10], "start": start, "p10_w": p10, "p50_w": p50, "p90_w": p90}


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
