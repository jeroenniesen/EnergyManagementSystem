from datetime import UTC, datetime, timedelta

from ems.timeseries import observed_segments

START = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)


def _row(minutes: int, watts: float) -> dict:
    return {"ts": (START + timedelta(minutes=minutes)).isoformat(), "power_w": watts}


def test_segments_use_actual_sample_spacing_and_bound_the_final_hold():
    segments = observed_segments(
        [_row(0, 1000), _row(5, 2000), _row(17, 3000)],
        start=START,
        end=START + timedelta(minutes=30),
        fields=("power_w",),
        nominal_interval_seconds=300,
        max_hold_seconds=600,
    )

    assert [(s.start, s.end, s.values["power_w"]) for s in segments] == [
        (START, START + timedelta(minutes=5), 1000.0),
        (START + timedelta(minutes=5), START + timedelta(minutes=15), 2000.0),
        (START + timedelta(minutes=17), START + timedelta(minutes=22), 3000.0),
    ]
    assert sum(s.duration_seconds for s in segments) == 20 * 60


def test_segments_average_duplicates_sort_input_and_split_quarter_boundaries():
    segments = observed_segments(
        [_row(14, 3000), _row(0, 1000), _row(14, 1000)],
        start=START,
        end=START + timedelta(minutes=30),
        fields=("power_w",),
        nominal_interval_seconds=300,
        max_hold_seconds=900,
    )

    assert [(s.start.minute, s.end.minute, s.values["power_w"]) for s in segments] == [
        (0, 14, 1000.0),
        (14, 15, 2000.0),
        (15, 19, 2000.0),
    ]


def test_segments_do_not_backfill_before_first_sample_or_bridge_long_gap():
    segments = observed_segments(
        [_row(5, 1000), _row(25, 2000)],
        start=START,
        end=START + timedelta(minutes=30),
        fields=("power_w",),
        nominal_interval_seconds=300,
        max_hold_seconds=600,
    )

    assert segments[0].start == START + timedelta(minutes=5)
    assert segments[0].end == START + timedelta(minutes=15)
    assert segments[1].start == START + timedelta(minutes=25)
    assert segments[1].end == START + timedelta(minutes=30)


def test_segments_ignore_malformed_non_finite_and_out_of_window_rows():
    rows = [
        _row(-1, 1),
        {"ts": "bad", "power_w": 2},
        {"ts": START.isoformat(), "power_w": float("nan")},
        _row(1, 1000),
        _row(31, 3),
    ]

    segments = observed_segments(
        rows,
        start=START,
        end=START + timedelta(minutes=30),
        fields=("power_w",),
        nominal_interval_seconds=300,
    )

    assert len(segments) == 1
    assert segments[0].start == START + timedelta(minutes=1)
    assert segments[0].values == {"power_w": 1000.0}
