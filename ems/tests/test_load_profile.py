"""Expected house-load profile learned from recent history (time-of-day average), used to drive
the 24h energy projection. Pure — canned rows, no DB."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.planner.load_profile import build_load_profile

AMS = ZoneInfo("Europe/Amsterdam")


def _row(iso: str, load: float) -> dict:
    return {"ts": iso, "house_load_w": load}


def test_hourly_average_is_used_for_a_well_sampled_hour():
    # Three samples in the 20:00 local hour (summer = UTC+2 -> 18:00Z) -> their mean.
    rows = [
        _row("2026-06-20T18:00:00+00:00", 600.0),
        _row("2026-06-20T18:20:00+00:00", 800.0),
        _row("2026-06-20T18:40:00+00:00", 1000.0),
    ]
    prof = build_load_profile(rows, AMS, fallback_w=500.0, min_samples=3)
    # A time in the 20:00 local hour gets the hourly mean (800), regardless of date.
    assert prof.expected_w(datetime(2026, 6, 28, 18, 5, tzinfo=UTC)) == 800.0


def test_sparse_hour_falls_back_to_the_overall_mean():
    rows = [
        _row("2026-06-20T18:00:00+00:00", 600.0),
        _row("2026-06-20T18:20:00+00:00", 800.0),
        _row("2026-06-20T18:40:00+00:00", 1000.0),
        _row("2026-06-20T05:00:00+00:00", 300.0),  # lone sample in another hour
    ]
    prof = build_load_profile(rows, AMS, fallback_w=999.0, min_samples=3)
    # 07:00 local (05:00Z) has only one sample (< min_samples) -> overall mean, not fallback_w.
    overall = (600 + 800 + 1000 + 300) / 4
    assert prof.expected_w(datetime(2026, 6, 28, 5, 30, tzinfo=UTC)) == overall


def test_no_history_uses_the_caller_fallback():
    prof = build_load_profile([], AMS, fallback_w=450.0)
    assert prof.expected_w(datetime(2026, 6, 28, 12, 0, tzinfo=UTC)) == 450.0


def test_malformed_rows_are_ignored():
    rows = [
        _row("not-a-date", 700.0),
        {"ts": "2026-06-20T10:00:00+00:00"},  # missing load
        _row("2026-06-20T10:10:00+00:00", 400.0),
        _row("2026-06-20T10:20:00+00:00", 400.0),
        _row("2026-06-20T10:30:00+00:00", 400.0),
    ]
    prof = build_load_profile(rows, AMS, fallback_w=0.0, min_samples=3)
    # Only the three valid 12:00-local samples count.
    assert prof.expected_w(datetime(2026, 6, 28, 10, 0, tzinfo=UTC)) == 400.0


def test_naive_timestamps_are_treated_as_utc():
    rows = [_row("2026-06-20T18:00:00", 700.0)] * 3  # no tz offset
    prof = build_load_profile(rows, AMS, fallback_w=0.0, min_samples=3)
    # 18:00 naive == 18:00Z == 20:00 local -> bucketed at local hour 20.
    assert prof.expected_w(datetime(2026, 6, 28, 18, 0, tzinfo=UTC)) == 700.0
