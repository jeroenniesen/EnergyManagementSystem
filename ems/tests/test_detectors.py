"""BACKLOG B-75 — pure forecast-driven detectors (`ems/detectors.py`). Each detector is exercised
at its exact trigger/no-trigger boundary: the 40%/30% thresholds, the 17:00-21:00 evening window,
the 3h EV plug-in horizon, and the confidence gate on `evening_peak_risk`. `typical_daily_solar_kwh`
(the caller-side baseline helper) gets its own section at the bottom."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.detectors import (
    ev_plug_in_reminder,
    evening_peak_risk,
    low_solar_tomorrow,
    price_opportunity,
    typical_daily_solar_kwh,
)
from ems.sources.prices import PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")


def _local(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=AMS)


# ---------------------------------------------------------------------------------------------
# low_solar_tomorrow
# ---------------------------------------------------------------------------------------------

def _p50_kwh(kwh: float, tomorrow: datetime, *, hours: float = 4.0) -> dict[datetime, float]:
    """A flat block of 15-min forecast slots (`hours` long) whose total sums exactly to `kwh`."""
    n = int(hours * 4)
    w = kwh * 1000.0 / hours
    return {tomorrow.replace(hour=10) + timedelta(minutes=15 * i): w for i in range(n)}


def test_low_solar_tomorrow_fires_when_forecast_under_40pct_of_typical():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    result = low_solar_tomorrow(_p50_kwh(3.9, tomorrow), 10.0, now=now)
    assert result is not None
    assert result["key"] == "low_solar_tomorrow"
    assert "Grey day tomorrow" in result["title"]
    assert "3.9" in result["body"] and "10.0" in result["body"]
    assert result["confidence"] == "medium"
    assert result["dedupe_key"] == "low_solar:2026-07-13"
    assert len(result["body"]) <= 200


def test_low_solar_tomorrow_no_fire_exactly_at_40pct_boundary():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    # Exactly 40% of typical must NOT fire (strictly-less-than threshold).
    assert low_solar_tomorrow(_p50_kwh(4.0, tomorrow), 10.0, now=now) is None


def test_low_solar_tomorrow_no_fire_just_above_threshold():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    assert low_solar_tomorrow(_p50_kwh(4.01, tomorrow), 10.0, now=now) is None


def test_low_solar_tomorrow_evening_window_boundaries():
    tomorrow = _local(2026, 7, 13, 0, 0)
    p50 = _p50_kwh(1.0, tomorrow)  # well under 40% of 10 kWh
    assert low_solar_tomorrow(p50, 10.0, now=_local(2026, 7, 12, 16, 59)) is None
    assert low_solar_tomorrow(p50, 10.0, now=_local(2026, 7, 12, 17, 0)) is not None
    assert low_solar_tomorrow(p50, 10.0, now=_local(2026, 7, 12, 20, 59)) is not None
    assert low_solar_tomorrow(p50, 10.0, now=_local(2026, 7, 12, 21, 0)) is None


def test_low_solar_tomorrow_no_fire_on_empty_forecast():
    now = _local(2026, 7, 12, 18, 0)
    assert low_solar_tomorrow({}, 10.0, now=now) is None


def test_low_solar_tomorrow_no_fire_without_a_baseline():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    assert low_solar_tomorrow(_p50_kwh(1.0, tomorrow), None, now=now) is None
    assert low_solar_tomorrow(_p50_kwh(1.0, tomorrow), 0.0, now=now) is None


# ---------------------------------------------------------------------------------------------
# ev_plug_in_reminder
# ---------------------------------------------------------------------------------------------

def _plan(start: datetime, kwh: float = 5.0) -> dict:
    end = start + timedelta(hours=1)
    return {"windows": [{"start": start.isoformat(), "end": end.isoformat(), "battery_kwh": kwh}]}


def test_ev_plug_in_reminder_fires_when_window_starts_soon_and_car_not_charging():
    now = _local(2026, 7, 12, 20, 0)
    start = now + timedelta(hours=1)
    result = ev_plug_in_reminder(_plan(start, kwh=6.2), False, now=now)
    assert result is not None
    assert result["key"] == "ev_plug_in"
    assert "21:00" in result["body"] and "6.2" in result["body"]
    assert result["confidence"] == "high"
    assert result["dedupe_key"] == f"ev_plug_in:{start.isoformat()}"


def test_ev_plug_in_reminder_boundary_exactly_3h_fires():
    now = _local(2026, 7, 12, 20, 0)
    start = now + timedelta(hours=3)
    assert ev_plug_in_reminder(_plan(start), False, now=now) is not None


def test_ev_plug_in_reminder_boundary_just_over_3h_no_fire():
    now = _local(2026, 7, 12, 20, 0)
    start = now + timedelta(hours=3, minutes=1)
    assert ev_plug_in_reminder(_plan(start), False, now=now) is None


def test_ev_plug_in_reminder_no_fire_when_already_charging():
    now = _local(2026, 7, 12, 20, 0)
    start = now + timedelta(minutes=30)
    assert ev_plug_in_reminder(_plan(start), True, now=now) is None


def test_ev_plug_in_reminder_no_fire_without_a_plan():
    now = _local(2026, 7, 12, 20, 0)
    assert ev_plug_in_reminder(None, False, now=now) is None
    assert ev_plug_in_reminder({"windows": []}, False, now=now) is None


# ---------------------------------------------------------------------------------------------
# evening_peak_risk
# ---------------------------------------------------------------------------------------------

def test_evening_peak_risk_fires_when_shortfall_exceeds_5pp():
    now = _local(2026, 7, 12, 15, 0)
    result = evening_peak_risk(44.9, 50.0, "medium", now=now)
    assert result is not None
    assert result["key"] == "peak_risk"
    assert "45%" in result["body"] and "50%" in result["body"]
    assert result["confidence"] == "medium"
    assert result["dedupe_key"] == "peak_risk:2026-07-12"


def test_evening_peak_risk_no_fire_exactly_at_5pp_boundary():
    assert evening_peak_risk(45.0, 50.0, "medium", now=_local(2026, 7, 12, 15, 0)) is None


def test_evening_peak_risk_no_fire_when_confidence_is_low():
    # A big shortfall, but low confidence must suppress the alarm (don't alarm on bad data).
    assert evening_peak_risk(20.0, 50.0, "low", now=_local(2026, 7, 12, 15, 0)) is None


def test_evening_peak_risk_fires_with_high_confidence():
    assert evening_peak_risk(20.0, 50.0, "high", now=_local(2026, 7, 12, 15, 0)) is not None


def test_evening_peak_risk_no_fire_on_missing_inputs():
    now = _local(2026, 7, 12, 15, 0)
    assert evening_peak_risk(None, 50.0, "medium", now=now) is None
    assert evening_peak_risk(20.0, None, "medium", now=now) is None
    assert evening_peak_risk(20.0, 50.0, None, now=now) is None


# ---------------------------------------------------------------------------------------------
# price_opportunity
# ---------------------------------------------------------------------------------------------

def _flat_slots(tomorrow: datetime, price: float, n: int = 96) -> list[PriceSlot]:
    return [PriceSlot(start=tomorrow + timedelta(minutes=15 * i), eur_per_kwh=price)
            for i in range(n)]


def test_price_opportunity_fires_on_negative_price_slot():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    slots = _flat_slots(tomorrow, 0.20)
    slots[10] = PriceSlot(start=tomorrow + timedelta(minutes=150), eur_per_kwh=-0.02)
    result = price_opportunity(slots, now=now)
    assert result is not None
    assert result["key"] == "price_opportunity"
    assert "-0.02" in result["body"]
    assert result["confidence"] == "high"
    assert result["dedupe_key"] == "price_opp:2026-07-13"


def test_price_opportunity_fires_when_min_under_30pct_of_average():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    slots = _flat_slots(tomorrow, 0.20)
    slots[5] = PriceSlot(start=tomorrow + timedelta(minutes=75), eur_per_kwh=0.05)  # < 30% of ~0.20
    result = price_opportunity(slots, now=now)
    assert result is not None


def test_price_opportunity_no_fire_exactly_at_30pct_boundary():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    avg = 0.20
    slots = _flat_slots(tomorrow, avg)
    slots[5] = PriceSlot(start=tomorrow + timedelta(minutes=75), eur_per_kwh=round(0.3 * avg, 10))
    assert price_opportunity(slots, now=now) is None


def test_price_opportunity_no_fire_just_under_30pct_boundary_fires():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    avg = 0.20
    slots = _flat_slots(tomorrow, avg)
    slots[5] = PriceSlot(start=tomorrow + timedelta(minutes=75), eur_per_kwh=0.3 * avg - 0.001)
    assert price_opportunity(slots, now=now) is not None


def test_price_opportunity_evening_window_boundaries():
    tomorrow = _local(2026, 7, 13, 0, 0)
    slots = _flat_slots(tomorrow, 0.20)
    slots[5] = PriceSlot(start=tomorrow + timedelta(minutes=75), eur_per_kwh=-0.01)
    assert price_opportunity(slots, now=_local(2026, 7, 12, 16, 59)) is None
    assert price_opportunity(slots, now=_local(2026, 7, 12, 17, 0)) is not None
    assert price_opportunity(slots, now=_local(2026, 7, 12, 21, 0)) is None


def test_price_opportunity_no_fire_on_empty_slots():
    assert price_opportunity([], now=_local(2026, 7, 12, 18, 0)) is None


def test_price_opportunity_no_fire_when_flat_and_unremarkable():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    assert price_opportunity(_flat_slots(tomorrow, 0.20), now=now) is None


def test_price_opportunity_reports_the_cheapest_contiguous_run():
    now = _local(2026, 7, 12, 18, 0)
    tomorrow = _local(2026, 7, 13, 0, 0)
    slots = _flat_slots(tomorrow, 0.20)
    # Two separate cheap runs; the second (more negative) is cheaper and should be reported.
    slots[4] = PriceSlot(start=tomorrow + timedelta(minutes=60), eur_per_kwh=-0.01)
    slots[40] = PriceSlot(start=tomorrow + timedelta(minutes=600), eur_per_kwh=-0.05)
    slots[41] = PriceSlot(start=tomorrow + timedelta(minutes=615), eur_per_kwh=-0.05)
    result = price_opportunity(slots, now=now)
    assert result is not None
    assert "10:00" in result["body"]  # the -0.05 run starts at 10:00 (minute 600)


# ---------------------------------------------------------------------------------------------
# typical_daily_solar_kwh
# ---------------------------------------------------------------------------------------------

def _rows_for_day(day, watts: float, *, hours: float = 4.0) -> list[dict]:
    """15-min-spaced raw rows covering `hours` starting at 10:00 local, flat at `watts` — matches
    `typical_daily_solar_kwh`'s 15-min-bucket integration exactly, so the resulting daily kWh is
    `watts * hours / 1000`."""
    n = int(hours * 4)
    start_local = datetime(day.year, day.month, day.day, 10, tzinfo=AMS)
    out = []
    for i in range(n):
        ts = (start_local + timedelta(minutes=15 * i)).astimezone(ZoneInfo("UTC"))
        out.append({"ts": ts.isoformat(), "solar_power_w": watts})
    return out


def test_typical_daily_solar_kwh_median_of_available_days():
    from datetime import date
    today = date(2026, 7, 12)
    rows = []
    # Day 1: flat 1000 W for 4 hours -> 4 kWh. Day 2: flat 2000 W for 4 hours -> 8 kWh.
    rows += _rows_for_day(date(2026, 7, 10), 1000.0)
    rows += _rows_for_day(date(2026, 7, 11), 2000.0)
    result = typical_daily_solar_kwh(rows, AMS, today)
    assert result == 6.0  # median of [4.0, 8.0]


def test_typical_daily_solar_kwh_excludes_today():
    from datetime import date
    today = date(2026, 7, 12)
    rows = _rows_for_day(today, 5000.0)  # today only — must be ignored
    assert typical_daily_solar_kwh(rows, AMS, today) is None


def test_typical_daily_solar_kwh_excludes_days_older_than_window():
    from datetime import date
    today = date(2026, 7, 20)
    old_day = today - timedelta(days=20)  # outside the default 14-day window
    rows = _rows_for_day(old_day, 5000.0)
    assert typical_daily_solar_kwh(rows, AMS, today) is None


def test_typical_daily_solar_kwh_no_data_returns_none():
    assert typical_daily_solar_kwh([], AMS, date_today()) is None


def date_today():
    from datetime import date
    return date(2026, 7, 12)
