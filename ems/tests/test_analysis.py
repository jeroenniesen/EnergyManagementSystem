"""Forecast skill (pure): actual-vs-forecast solar error over matched 15-min slots."""
from ems.analysis import forecast_error


def _forecast_row(start: str, p10: float, p50: float, p90: float) -> dict:
    return {"issued_date": start[:10], "start": start, "p10_w": p10, "p50_w": p50, "p90_w": p90}


def _raw_row(ts: str, solar_w: float) -> dict:
    return {"ts": ts, "solar_power_w": solar_w}


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
