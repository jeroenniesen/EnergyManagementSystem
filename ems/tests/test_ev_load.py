"""EV exogenous estimate for winter sizing (#181) — pure, fail-soft, no hardware."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from ems.ev_schedule import default_schedule
from ems.planner.ev_load import estimate_ev_exogenous_kwh, format_ev_load_reason

AMS = ZoneInfo("Europe/Amsterdam")
# Monday 2026-01-12 local
NOW = datetime(2026, 1, 12, 10, 0, tzinfo=AMS)


def test_format_reason_canonical():
    assert format_ev_load_reason(27.4) == "EV load expected ~27 kWh"


def test_fail_soft_when_nothing_known():
    est = estimate_ev_exogenous_kwh(now=NOW, tz=AMS)
    assert est.expected_kwh == 0.0
    assert est.source == "none"


def test_manual_expected_day_kwh_wins():
    est = estimate_ev_exogenous_kwh(
        now=NOW, tz=AMS, expected_day_kwh=30.0, day_hint=True, typical_charge_kwh=20.0,
    )
    assert est.expected_kwh == 30.0
    assert est.source == "manual"
    assert "EV load expected ~30 kWh" in est.reason


def test_day_hint_uses_typical_charge_kwh():
    est = estimate_ev_exogenous_kwh(
        now=NOW, tz=AMS, day_hint=True, typical_charge_kwh=22.0,
    )
    assert est.expected_kwh == 22.0
    assert est.source == "day_hint"
    assert "EV load expected ~22 kWh" in est.reason


def test_schedule_enabled_weekday_is_day_type_heuristic():
    sched = default_schedule()
    sched["mon"]["enabled"] = True  # NOW is a Monday
    est = estimate_ev_exogenous_kwh(
        now=NOW, tz=AMS, typical_charge_kwh=18.0, schedule_raw=json.dumps(sched),
    )
    assert est.expected_kwh == 18.0
    assert est.source == "schedule"


def test_same_weekday_history_median():
    # Two prior Mondays with significant EV; today excluded.
    rows = [
        {"date": "2025-12-29", "ev_kwh": 28.0},  # Mon
        {"date": "2026-01-05", "ev_kwh": 32.0},  # Mon
        {"date": "2026-01-06", "ev_kwh": 1.0},   # Tue — ignored
        {"date": "2026-01-12", "ev_kwh": 99.0},  # today — ignored
    ]
    est = estimate_ev_exogenous_kwh(now=NOW, tz=AMS, daily_ev_rows=rows)
    assert est.source == "same_weekday_history"
    assert est.expected_kwh == 30.0  # median of 28, 32
    assert "EV load expected ~30 kWh" in est.reason


def test_sparse_history_fail_soft():
    rows = [{"date": "2026-01-05", "ev_kwh": 28.0}]  # only one Monday sample
    est = estimate_ev_exogenous_kwh(now=NOW, tz=AMS, daily_ev_rows=rows)
    assert est.expected_kwh == 0.0
    assert est.source == "none"


def test_corrupt_inputs_never_raise():
    est = estimate_ev_exogenous_kwh(
        now=NOW, tz=AMS, expected_day_kwh="nope",  # type: ignore[arg-type]
        schedule_raw="{not-json", daily_ev_rows=[{"date": "bad", "ev_kwh": "x"}],
    )
    assert est.expected_kwh == 0.0
