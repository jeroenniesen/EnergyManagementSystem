"""B-64 / #81 — enhanced household load profile (weekday/weekend, season, weather) + held-out
accuracy vs. the naive hour-of-day baseline. Logic sliced from draft PR #65; no bill-minimization
code, no real devices.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ems.calibration import attach_solar, load_accuracy, rows_from_raw_samples
from ems.planner.load_profile import (
    build_load_profile,
    meteorological_season,
    weather_bin_from_solar,
)

TZ = ZoneInfo("UTC")
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)  # Monday


def test_meteorological_season_covers_all_months():
    assert meteorological_season(12) == "winter"
    assert meteorological_season(3) == "spring"
    assert meteorological_season(7) == "summer"
    assert meteorological_season(10) == "autumn"


def test_weather_bin_from_solar_uses_tercile_cuts():
    assert weather_bin_from_solar(100.0, (200.0, 800.0)) == "dim"
    assert weather_bin_from_solar(500.0, (200.0, 800.0)) == "mixed"
    assert weather_bin_from_solar(900.0, (200.0, 800.0)) == "bright"


def test_enhanced_profile_separates_weekends_without_future_leakage():
    """Adapted from PR #65 `test_bill_load_profile` — weekday/weekend feature + no future leak."""
    rows = []
    for i in range(28):
        ts = NOW - timedelta(days=i + 1)
        rows.append({"ts": ts.isoformat(), "non_ev_load_w": 900 if ts.weekday() >= 5 else 300})
    rows += [{"ts": (NOW + timedelta(days=1)).isoformat(), "non_ev_load_w": 10000}] * 100
    profile = build_load_profile(rows, TZ, enhanced=True, as_of=NOW)
    assert "weekday_weekend" in profile.features
    assert "season" in profile.features
    assert profile.expected_w(NOW) == pytest.approx(300)
    assert profile.expected_w(NOW + timedelta(days=5)) == pytest.approx(900)
    low, high = profile.band_w(NOW)
    assert 0 <= low <= 300 <= high


def test_enhanced_profile_weights_recent_days_and_not_sample_count():
    """Adapted from PR #65 — recency weighting beats raw sample-count dominance."""
    rows = []
    for i in range(1, 29):
        ts = NOW - timedelta(days=i)
        rows += [{"ts": ts.isoformat(), "non_ev_load_w": 800 if i <= 7 else 200}] * (
            20 if i > 7 else 1
        )
    profile = build_load_profile(rows, TZ, enhanced=True, as_of=NOW)
    assert 350 < profile.expected_w(NOW) < 800


def test_enhanced_cold_start_keeps_typical_shape():
    midnight = datetime(2026, 9, 14, tzinfo=UTC)
    p = build_load_profile([], TZ, enhanced=True, as_of=midnight)
    assert p.expected_w(midnight) == 250
    assert p.band_w(midnight)[1] > p.expected_w(midnight)
    assert p.features == ("hour", "weekday_weekend", "season")


def test_enhanced_requires_as_of():
    with pytest.raises(ValueError, match="as_of"):
        build_load_profile([], TZ, enhanced=True)


def test_enhanced_profile_uses_season_feature():
    """Same weekday+hour, opposite seasons → different learned means when season is a feature."""
    rows = []
    # Stay inside the enhanced 42-day window: three May (spring) + three June (summer) Mondays.
    as_of = datetime(2026, 6, 22, tzinfo=UTC)
    for day in (
        datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
    ):
        rows.append({"ts": day.isoformat(), "non_ev_load_w": 1200.0})
    for day in (
        datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 6, 8, 12, 0, tzinfo=UTC),
        datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
    ):
        rows.append({"ts": day.isoformat(), "non_ev_load_w": 400.0})
    profile = build_load_profile(rows, TZ, enhanced=True, as_of=as_of, min_samples=3)
    assert "season" in profile.features
    spring_monday = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    summer_monday = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    assert profile.expected_w(spring_monday) == pytest.approx(1200.0)
    assert profile.expected_w(summer_monday) == pytest.approx(400.0)


def test_enhanced_profile_uses_weather_feature_from_solar():
    """Dim vs bright daytime solar on otherwise identical weekdays → different load forecasts."""
    rows = []
    as_of = datetime(2026, 7, 20, tzinfo=UTC)
    # 6 dull + 6 bright Mondays inside the 42-day window (≥3 daily samples per weather bin).
    for week in range(12):
        day = datetime(2026, 6, 8, tzinfo=UTC) + timedelta(weeks=week)  # Mondays
        dull = week % 2 == 0
        load = 900.0 if dull else 500.0
        solar = 50.0 if dull else 900.0
        for hour in (10, 11, 12, 13, 14):
            ts = day + timedelta(hours=hour)
            rows.append({
                "ts": ts.isoformat(),
                "non_ev_load_w": load,
                "solar_power_w": solar,
            })
    profile = build_load_profile(rows, TZ, enhanced=True, as_of=as_of, min_samples=3)
    assert "weather" in profile.features
    assert profile.weather_thresholds is not None
    probe = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)  # a Monday noon
    dim = profile.expected_w(probe, daytime_solar_w=50.0)
    bright = profile.expected_w(probe, daytime_solar_w=900.0)
    assert dim == pytest.approx(900.0)
    assert bright == pytest.approx(500.0)


def test_load_accuracy_uses_held_out_days_and_beats_naive_baseline():
    """Adapted from PR #65 — enhanced MAE beats hour-only baseline when weekend load differs."""
    rows = []
    for days in range(1, 43):
        for hour in range(24):
            t = NOW - timedelta(days=days) + timedelta(hours=hour)
            rows.append({"ts": t.isoformat(), "non_ev_load_w": 900 if t.weekday() >= 5 else 300})
    result = load_accuracy(rows, now=NOW, tz=TZ)
    assert result["available"] is True
    assert result["hours_scored"] >= 24
    assert result["enhanced_mae_w"] < result["baseline_mae_w"]
    assert result["improves_on_baseline"] is True
    assert "weekday_weekend" in result["features"]
    assert "season" in result["features"]
    assert load_accuracy([], now=NOW, tz=TZ)["available"] is False


def test_load_accuracy_with_season_and_weather_beats_baseline():
    """Season + weather signal must improve held-out MAE vs. hour-only recent average."""
    rows = []
    now = datetime(2026, 8, 10, tzinfo=UTC)
    for days in range(1, 50):
        day = now - timedelta(days=days)
        season_bump = 400.0 if meteorological_season(day.month) == "winter" else 0.0
        for hour in range(24):
            t = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            # Weekend bump + dull-day bump so enhanced features have something to learn.
            weekend = 500.0 if t.weekday() >= 5 else 0.0
            dull = (days % 3 == 0)
            weather_bump = 200.0 if dull else 0.0
            solar = 40.0 if dull else 700.0
            load = 300.0 + weekend + season_bump + weather_bump
            rows.append({
                "ts": t.isoformat(),
                "non_ev_load_w": load,
                "solar_power_w": solar if 8 <= hour < 17 else 0.0,
            })
    result = load_accuracy(rows, now=now, tz=TZ)
    assert result["available"] is True
    assert result["improves_on_baseline"] is True
    assert result["enhanced_mae_w"] < result["baseline_mae_w"]
    assert "weather" in result["features"] or "season" in result["features"]


def test_attach_solar_and_rows_from_raw_preserve_weather_proxy():
    derived = [{"ts": "2026-06-01T12:00:00+00:00", "non_ev_load_w": 400.0}]
    raw = [{
        "ts": "2026-06-01T12:00:00+00:00",
        "grid_power_w": 100.0,
        "solar_power_w": 800.0,
        "battery_power_w": 0.0,
        "ev_power_w": 0.0,
    }]
    attached = attach_solar(derived, raw)
    assert attached[0]["solar_power_w"] == 800.0
    rebuilt = rows_from_raw_samples(raw)
    assert rebuilt[0]["non_ev_load_w"] == pytest.approx(900.0)
    assert rebuilt[0]["solar_power_w"] == 800.0
