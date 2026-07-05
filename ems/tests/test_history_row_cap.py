"""Cadence-aware history row caps (finding 10).

The finance/report endpoints must size their row limits to the ACTUAL recorder cadence, not a
hardcoded 3000/day or a 1-row-per-minute ceiling, so reporting stays correct if the sampling
frequency changes.
"""
from ems.web.api import history_row_cap

DAY = 86_400.0


def test_scales_with_cadence_so_a_finer_rate_is_not_truncated():
    # Production 5-min cadence → ~288 rows/day; a 1-min cadence → ~1440. The finer rate MUST get a
    # bigger cap, or its rows get silently dropped.
    coarse = history_row_cap(DAY, 300.0)
    fine = history_row_cap(DAY, 60.0)
    assert fine > coarse
    # Enough headroom to hold every row at the stated cadence.
    assert history_row_cap(DAY, 60.0) >= 1440
    assert history_row_cap(DAY, 5.0) >= 17_280   # fast dev cadence must not truncate a day


def test_scales_with_window_length():
    day = history_row_cap(DAY, 300.0)
    week = history_row_cap(7 * DAY, 300.0)
    assert week > day


def test_floor_keeps_a_sane_minimum_for_tiny_windows():
    # A near-empty window still allows a healthy batch (never a silly tiny limit).
    assert history_row_cap(60.0, 300.0) >= 1000


def test_ceiling_bounds_the_query():
    # A pathological huge window is clamped, not unbounded.
    assert history_row_cap(10_000 * DAY, 60.0) == 200_000


def test_zero_cadence_is_safe():
    # A misconfigured 0s cadence must not divide-by-zero; it clamps to a 1s cadence internally.
    cap = history_row_cap(DAY, 0.0)
    assert 86_400 <= cap <= 200_000


def test_negative_span_is_safe():
    assert history_row_cap(-100.0, 300.0) >= 1000
