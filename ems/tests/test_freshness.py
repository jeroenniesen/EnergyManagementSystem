from datetime import UTC, datetime, timedelta

import pytest

from ems.freshness import (
    Freshness,
    FreshnessTracker,
    StaleMeterHysteresis,
    classify,
    stale_flags,
)

T0 = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)


def test_classify_missing_fresh_stale():
    assert classify(None, T0, stale_after_s=600) is Freshness.MISSING
    assert classify(T0 - timedelta(seconds=60), T0, stale_after_s=600) is Freshness.FRESH
    assert classify(T0 - timedelta(seconds=700), T0, stale_after_s=600) is Freshness.STALE


def test_classify_clock_skew_future_is_fresh():
    # A future-timestamped reading (clock skew) is treated as fresh, not negative-age.
    assert classify(T0 + timedelta(seconds=30), T0, stale_after_s=600) is Freshness.FRESH


def test_tracker_mark_and_state_per_signal():
    tr = FreshnessTracker(stale_after_s=600)
    tr.mark("grid", T0 - timedelta(seconds=10))
    tr.mark("solar", T0 - timedelta(seconds=900))
    assert tr.state("grid", T0) is Freshness.FRESH
    assert tr.state("solar", T0) is Freshness.STALE
    assert tr.state("battery", T0) is Freshness.MISSING  # never marked


def test_tracker_age_and_snapshot():
    tr = FreshnessTracker(stale_after_s=600)
    tr.mark("grid", T0 - timedelta(seconds=120))
    assert tr.age_seconds("grid", T0) == 120
    assert tr.age_seconds("absent", T0) is None
    snap = tr.snapshot(T0)
    assert snap == {"grid": "fresh"}


def test_snapshot_surfaces_registered_missing_signals():
    # A registered signal that never reported must surface as MISSING, not be omitted (SPEC §4.7).
    tr = FreshnessTracker(stale_after_s=600)
    tr.register("grid", "solar", "soc")
    tr.mark("grid", T0 - timedelta(seconds=10))
    snap = tr.snapshot(T0)
    assert snap["grid"] == "fresh"
    assert snap["solar"] == "missing"
    assert snap["soc"] == "missing"


def test_stale_meter_hysteresis_clears_after_two_fresh_cycles():
    h = StaleMeterHysteresis()
    stale_snap = {"ev": "stale", "grid": "fresh"}
    assert h.stale_signals(stale_snap) == {"ev"}
    fresh_snap = {"ev": "fresh", "grid": "fresh"}
    assert h.stale_signals(fresh_snap) == {"ev"}
    assert h.stale_signals(fresh_snap) == set()


def test_stale_meter_hysteresis_flap_reset_needs_two_fresh_after_re_stale():
    h = StaleMeterHysteresis()
    ev_stale = {"ev": "stale", "grid": "fresh"}
    ev_fresh = {"ev": "fresh", "grid": "fresh"}
    assert h.stale_signals(ev_stale) == {"ev"}
    assert h.stale_signals(ev_fresh) == {"ev"}
    assert h.stale_signals(ev_stale) == {"ev"}
    assert h.stale_signals(ev_fresh) == {"ev"}
    assert h.stale_signals(ev_fresh) == set()


def test_stale_meter_hysteresis_flap_reset_grid_signal():
    h = StaleMeterHysteresis()
    grid_stale = {"ev": "fresh", "grid": "stale"}
    grid_fresh = {"ev": "fresh", "grid": "fresh"}
    assert h.stale_signals(grid_stale) == {"grid"}
    assert h.stale_signals(grid_fresh) == {"grid"}
    assert h.stale_signals(grid_stale) == {"grid"}
    assert h.stale_signals(grid_fresh) == {"grid"}
    assert h.stale_signals(grid_fresh) == set()


def test_missing_meter_not_stale_until_ever_fresh():
    h = StaleMeterHysteresis()
    assert h.stale_signals({"ev": "missing", "grid": "fresh"}) == set()
    assert h.stale_signals({"ev": "fresh", "grid": "fresh"}) == set()
    assert h.stale_signals({"ev": "missing", "grid": "fresh"}) == {"ev"}


def test_stale_flags_callable_exception_treated_as_stale(caplog):
    def _boom(_now):
        raise RuntimeError("meter read failed")

    with caplog.at_level("WARNING"):
        assert stale_flags(_boom, T0) == (True, True)
    assert "stale_signals callable failed" in caplog.text


def test_stale_flags_none_is_not_stale():
    assert stale_flags(None, T0) == (False, False)


def test_naive_datetime_rejected():
    tr = FreshnessTracker()
    with pytest.raises(ValueError):
        tr.mark("grid", datetime(2026, 6, 27, 10, 0))  # naive
    with pytest.raises(ValueError):
        classify(None, datetime(2026, 6, 27, 10, 0), 600)  # naive now
