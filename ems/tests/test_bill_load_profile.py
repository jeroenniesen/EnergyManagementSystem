from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ems.planner.load_profile import build_load_profile


def test_enhanced_profile_separates_weekends_without_future_leakage():
    now = datetime(2026, 9, 14, tzinfo=UTC)
    rows = []
    for i in range(28):
        ts = now - timedelta(days=i + 1)
        rows.append({"ts": ts.isoformat(), "non_ev_load_w": 900 if ts.weekday() >= 5 else 300})
    rows += [{"ts": (now + timedelta(days=1)).isoformat(), "non_ev_load_w": 10000}] * 100
    profile = build_load_profile(rows, ZoneInfo("UTC"), enhanced=True, as_of=now)
    assert profile.expected_w(now) == pytest.approx(300)
    assert profile.expected_w(now + timedelta(days=5)) == pytest.approx(900)
    low, high = profile.band_w(now)
    assert 0 <= low <= 300 <= high


def test_enhanced_profile_weights_recent_days_and_not_sample_count():
    now = datetime(2026, 9, 14, tzinfo=UTC)
    rows = []
    for i in range(1, 29):
        ts = now - timedelta(days=i)
        rows += [{"ts": ts.isoformat(), "non_ev_load_w": 800 if i <= 7 else 200}] * (
            20 if i > 7 else 1
        )
    profile = build_load_profile(rows, ZoneInfo("UTC"), enhanced=True, as_of=now)
    assert 350 < profile.expected_w(now) < 800


def test_enhanced_cold_start_keeps_typical_shape():
    now = datetime(2026, 9, 14, tzinfo=UTC)
    p = build_load_profile([], ZoneInfo("UTC"), enhanced=True, as_of=now)
    assert p.expected_w(now) == 250
    assert p.band_w(now)[1] > p.expected_w(now)
