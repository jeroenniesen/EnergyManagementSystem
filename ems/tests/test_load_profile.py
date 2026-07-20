"""Expected house-load profile learned from recent history (time-of-day average), used to drive
the 24h energy projection. Pure — canned rows, no DB."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.planner.load_profile import build_load_profile

AMS = ZoneInfo("Europe/Amsterdam")


def _row(iso: str, load: float) -> dict:
    # Default profile field is non_ev_load_w (battery offsets the house, not the EV — SPEC §4.5).
    return {"ts": iso, "non_ev_load_w": load}


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


def test_sparse_hour_falls_back_to_the_typical_day_shape():
    rows = [
        _row("2026-06-20T18:00:00+00:00", 600.0),
        _row("2026-06-20T18:20:00+00:00", 800.0),
        _row("2026-06-20T18:40:00+00:00", 1000.0),
        _row("2026-06-20T05:00:00+00:00", 300.0),  # lone sample in another hour
    ]
    prof = build_load_profile(rows, AMS, min_samples=3)
    # 07:00 local (05:00Z) has only one sample (< min_samples) -> the typical morning value (600 W),
    # NOT a flat overall mean of the high evening samples.
    assert prof.expected_w(datetime(2026, 6, 28, 5, 30, tzinfo=UTC)) == 600.0


def test_no_history_uses_the_typical_day_shape():
    # No data at all -> a realistic shaped day, not a flat constant: low overnight < daytime.
    prof = build_load_profile([], AMS)
    assert prof.expected_w(datetime(2026, 6, 28, 12, 0, tzinfo=UTC)) == 400.0  # daytime base
    assert prof.expected_w(datetime(2026, 6, 28, 2, 0, tzinfo=UTC)) == 250.0  # overnight, lower


def test_cold_start_high_burst_is_not_projected_flat_across_the_day():
    # A handful of high samples (2754 W) in ONE hour must NOT become the whole-day load — the
    # daytime baseline stays low (so solar can exceed it and charge). This was the live bug.
    rows = [_row("2026-06-20T18:00:00+00:00", 2754.0)]  # one 20:00-local sample
    prof = build_load_profile(rows, AMS, min_samples=3)
    assert prof.expected_w(datetime(2026, 6, 28, 12, 0, tzinfo=UTC)) == 400.0  # midday stays sane


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


def test_default_field_is_non_ev_load_so_ev_charging_is_excluded():
    # Rows carry a huge house load (EV charging) AND a modest non-EV load. The profile must learn
    # the non-EV baseline, not the 11 kW car charge.
    rows = [{"ts": "2026-06-20T18:00:00+00:00", "house_load_w": 11000.0,
             "non_ev_load_w": 900.0} for _ in range(3)]
    prof = build_load_profile(rows, AMS, fallback_w=0.0, min_samples=3)
    assert prof.expected_w(datetime(2026, 6, 28, 18, 0, tzinfo=UTC)) == 900.0


def test_field_param_can_select_house_load():
    rows = [{"ts": "2026-06-20T18:00:00+00:00", "house_load_w": 11000.0,
             "non_ev_load_w": 900.0} for _ in range(3)]
    prof = build_load_profile(rows, AMS, fallback_w=0.0, min_samples=3, field="house_load_w")
    assert prof.expected_w(datetime(2026, 6, 28, 18, 0, tzinfo=UTC)) == 11000.0


def test_naive_timestamps_are_treated_as_utc():
    rows = [_row("2026-06-20T18:00:00", 700.0)] * 3  # no tz offset
    prof = build_load_profile(rows, AMS, fallback_w=0.0, min_samples=3)
    # 18:00 naive == 18:00Z == 20:00 local -> bucketed at local hour 20.
    assert prof.expected_w(datetime(2026, 6, 28, 18, 0, tzinfo=UTC)) == 700.0


def test_invalid_loads_are_quarantined_from_learning():
    rows = [
        _row("2026-06-20T18:00:00+00:00", -500.0),
        _row("2026-06-20T18:10:00+00:00", float("nan")),
        _row("2026-06-20T18:20:00+00:00", 100_000.0),
    ]
    prof = build_load_profile(rows, AMS, min_samples=3)
    assert prof.by_hour == {}
