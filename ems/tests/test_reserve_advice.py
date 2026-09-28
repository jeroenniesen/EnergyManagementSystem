"""B-67 / #74 — advice-only night-reserve recommendation.

Covers bounds, DST night demand (92/100 quarters), insufficient-data fallback, and the hard
guarantee that the API never mutates settings or calls ModeController.decide.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.lifecycle import Lifecycle
from ems.reserve_advice import (
    INSUFFICIENT_DATA_LABEL,
    iter_quarter_hours,
    night_demand_kwh,
    recommend_night_reserve,
)
from ems.sky import sun_times
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


# ---- pure helpers ---------------------------------------------------------------------------

def test_iter_quarter_hours_dst_spring_and_fall_nights():
    """Nights that contain the NL DST transitions must not assume a fixed 96-slot day.

    The calendar days themselves are 92 (2026-03-29) and 100 (2026-10-25) quarters; the overnight
    span that crosses those transitions is correspondingly shorter / longer in real elapsed time.
    """
    from ems.timeutil import day_slot_count

    assert day_slot_count(datetime(2026, 3, 29).date(), AMS) == 92
    assert day_slot_count(datetime(2026, 10, 25).date(), AMS) == 100

    _, spring_sunset = sun_times(52.13, 5.29, datetime(2026, 3, 28).date(), AMS)
    spring_sunrise, _ = sun_times(52.13, 5.29, datetime(2026, 3, 29).date(), AMS)
    _, fall_sunset = sun_times(52.13, 5.29, datetime(2026, 10, 24).date(), AMS)
    fall_sunrise, _ = sun_times(52.13, 5.29, datetime(2026, 10, 25).date(), AMS)
    assert None not in (spring_sunset, spring_sunrise, fall_sunset, fall_sunrise)

    spring_slots = list(iter_quarter_hours(spring_sunset, spring_sunrise))
    fall_slots = list(iter_quarter_hours(fall_sunset, fall_sunrise))
    assert len(spring_slots) == 45
    assert len(fall_slots) == 56
    # Fall-back night is a full extra hour longer than spring-forward for these sun windows.
    assert len(fall_slots) - len(spring_slots) >= 8


def test_night_demand_matches_slot_count_on_dst_nights():
    """Constant 1000 W → kWh = slot_count × 0.25 on 92- and 100-quarter transition nights."""
    load = lambda _t: 1000.0  # noqa: E731
    for day, expected_slots in (
        (datetime(2026, 3, 28).date(), 45),   # into spring-forward morning (92-slot day)
        (datetime(2026, 10, 24).date(), 56),  # into fall-back morning (100-slot day)
    ):
        _, sunset = sun_times(52.13, 5.29, day, AMS)
        sunrise, _ = sun_times(52.13, 5.29, day + timedelta(days=1), AMS)
        assert sunset is not None and sunrise is not None
        slots = list(iter_quarter_hours(sunset, sunrise))
        assert len(slots) == expected_slots
        assert night_demand_kwh(load, sunset, sunrise) == pytest.approx(expected_slots * 0.25)


def test_recommend_clamps_between_min_reserve_and_100():
    low = recommend_night_reserve(
        night_demand_kwh=0.0,
        usable_kwh=10.0,
        min_reserve_soc=20.0,
        night_reserve_kwh=0.0,
        overnight_load_kwh=0.0,
        round_trip_efficiency=1.0,
    )
    assert low["recommended_soc_pct"] == 20.0
    assert low["min_reserve_soc"] == 20.0
    assert low["automatic"] is False

    high = recommend_night_reserve(
        night_demand_kwh=100.0,
        usable_kwh=10.0,
        min_reserve_soc=15.0,
        night_reserve_kwh=5.0,
        overnight_load_kwh=6.0,
        round_trip_efficiency=1.0,
    )
    assert high["recommended_soc_pct"] == 100.0
    assert 15.0 <= high["recommended_soc_pct"] <= 100.0


def test_without_forecast_falls_back_to_standard_with_insufficient_data_label():
    result = recommend_night_reserve(
        night_demand_kwh=None,
        usable_kwh=10.8,
        min_reserve_soc=10.0,
        night_reserve_kwh=2.0,
        overnight_load_kwh=6.0,
        round_trip_efficiency=1.0,
    )
    assert result["label"] == INSUFFICIENT_DATA_LABEL
    assert result["label"] == "onvoldoende data"
    assert result["night_demand_kwh"] is None
    assert 10.0 <= result["recommended_soc_pct"] <= 100.0
    # Standard = (6+2)/10.8*100 + 10% floor composition via charge_need → ~84.1%
    assert result["recommended_soc_pct"] == result["current_target_soc_pct"]
    assert "usual reserve" in result["reason"]


def test_with_demand_explains_difference_from_configured_plan():
    result = recommend_night_reserve(
        night_demand_kwh=9.0,
        usable_kwh=10.0,
        min_reserve_soc=10.0,
        night_reserve_kwh=2.0,
        overnight_load_kwh=4.0,
        round_trip_efficiency=1.0,
    )
    assert result["label"] is None
    assert result["night_demand_kwh"] == 9.0
    assert result["recommended_soc_pct"] > result["current_target_soc_pct"]
    assert "9.0 kWh" in result["reason"]


# ---- API: no settings write, no ModeController.decide ---------------------------------------

def _app(tmp_path, *, controller=None, solar=True):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        tz=AMS,
        price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS) if solar else None,
        settings_store=SettingsStore(db),
        controller=controller,
    )


def test_api_does_not_mutate_settings_or_call_mode_controller(tmp_path, monkeypatch):
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    decide_calls: list[object] = []
    real_decide = controller.decide

    def _spy_decide(*args, **kwargs):
        decide_calls.append((args, kwargs))
        return real_decide(*args, **kwargs)

    monkeypatch.setattr(controller, "decide", _spy_decide)
    with TestClient(_app(tmp_path, controller=controller)) as c:
        before = c.get("/api/settings").json()
        night_before = before["values"]["battery.night_reserve_kwh"]
        min_before = before["values"]["battery.min_reserve_soc"]
        decide_calls.clear()  # ignore any startup/lifespan activity

        r = c.get("/api/advisor/reserve")
        assert r.status_code == 200
        body = r.json()
        assert body["automatic"] is False
        advice = body["advice"]
        assert "recommended_soc_pct" in advice
        assert "reason" in advice
        assert advice["automatic"] is False
        assert advice["min_reserve_soc"] <= advice["recommended_soc_pct"] <= 100.0

        after = c.get("/api/settings").json()
        assert after["values"] == before["values"]
        assert after["values"]["battery.night_reserve_kwh"] == night_before
        assert after["values"]["battery.min_reserve_soc"] == min_before
        assert decide_calls == []


def test_api_without_forecast_returns_insufficient_data_label(tmp_path):
    with TestClient(_app(tmp_path, solar=False)) as c:
        body = c.get("/api/advisor/reserve").json()
    advice = body["advice"]
    assert advice["label"] == "onvoldoende data"
    assert advice["night_demand_kwh"] is None
    assert 0 <= advice["recommended_soc_pct"] <= 100
