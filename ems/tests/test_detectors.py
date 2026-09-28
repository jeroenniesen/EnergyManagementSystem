"""BACKLOG B-75 — pure forecast-driven detectors (`ems/detectors.py`). Each detector is exercised
at its exact trigger/no-trigger boundary: the 40%/30% thresholds, the 17:00-21:00 evening window,
the 3h EV plug-in horizon, and the confidence gate on `evening_peak_risk`. `typical_daily_solar_kwh`
(the caller-side baseline helper) gets its own section at the bottom. Issue #128 adds
`device_unreachable` (threshold / flap / recovery / unconfirmed stand / dry-run copy)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.detectors import (
    STABLE_RECOVER_AFTER,
    DeviceUnreachableState,
    device_unreachable,
    ev_plug_in_reminder,
    evening_peak_risk,
    low_solar_tomorrow,
    open_incidents_from_notifications,
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
    assert "reserve advice" in result["body"]
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


# ---------------------------------------------------------------------------------------------
# device_unreachable (issue #128)
# ---------------------------------------------------------------------------------------------

def _unreachable(device, last_fresh, now, state, **kw):
    return device_unreachable(device, last_fresh, now=now, state=state, **kw)


def test_device_unreachable_fires_after_threshold():
    state = DeviceUnreachableState()
    last = _local(2026, 7, 12, 10, 0)
    now = last + timedelta(minutes=15)
    result = _unreachable("battery", last, now, state, last_known_stand="zelfverbruik")
    assert result is not None
    assert result["key"] == "battery_down"
    assert result["title"] == "Batterij reageert niet"
    assert "Al 15 minuten geen contact (sinds 10:00)" in result["body"]
    assert "Laatst bekende stand: zelfverbruik" in result["body"]
    assert "stroom en netwerk" in result["body"]
    assert result["dedupe_key"] == f"battery_down:{state.incident_since.isoformat()}"
    assert state.down_push_sent is True


def test_device_unreachable_threshold_is_configurable():
    state = DeviceUnreachableState()
    last = _local(2026, 7, 12, 10, 0)
    # Under a 20-min threshold, 15 min of silence must NOT fire.
    assert _unreachable(
        "p1", last, last + timedelta(minutes=15), state, threshold=timedelta(minutes=20),
    ) is None
    result = _unreachable(
        "p1", last, last + timedelta(minutes=20), state, threshold=timedelta(minutes=20),
        last_known_stand="120 W",
    )
    assert result is not None
    assert result["title"] == "P1-meter reageert niet"
    assert "Al 20 minuten geen contact" in result["body"]


def test_device_unreachable_flap_one_down_no_recovery():
    """Down → 2 min up → down again: one down-push (same incident), no recovery push."""
    state = DeviceUnreachableState()
    last = _local(2026, 7, 12, 10, 0)
    t_down = last + timedelta(minutes=15)
    down1 = _unreachable("battery", last, t_down, state, last_known_stand="laden")
    assert down1 is not None
    incident = state.incident_since
    dedupe = down1["dedupe_key"]

    # 2 minutes of contact — not yet stable recovery.
    t_up = t_down + timedelta(minutes=2)
    assert _unreachable("battery", t_up, t_up, state) is None
    assert state.incident_since == incident  # still same outage
    assert state.contact_since == t_up

    # Silence again past threshold from the brief contact — same incident_since / dedupe_key
    # (Notifier would suppress a second push); no recovery was ever emitted.
    t_down2 = t_up + timedelta(minutes=15)
    down2 = _unreachable("battery", t_up, t_down2, state, last_known_stand="laden")
    assert down2 is not None
    assert down2["dedupe_key"] == dedupe
    assert state.incident_since == incident
    assert state.contact_since is None  # flap reset


def test_device_unreachable_stable_recovery_one_up_push():
    state = DeviceUnreachableState()
    last = _local(2026, 7, 12, 10, 0)
    t_down = last + timedelta(minutes=15)
    assert _unreachable("battery", last, t_down, state) is not None

    t_contact = t_down + timedelta(minutes=1)
    assert _unreachable("battery", t_contact, t_contact, state, dry_run=False) is None

    t_stable = t_contact + STABLE_RECOVER_AFTER
    up = _unreachable("battery", t_stable, t_stable, state, dry_run=False)
    assert up is not None
    assert up["key"] == "battery_up"
    assert up["body"] == "Batterij weer bereikbaar, EMS stuurt weer."
    assert state.incident_since is None
    assert state.down_push_sent is False


def test_device_unreachable_no_recovery_without_prior_down_push():
    state = DeviceUnreachableState(
        incident_since=_local(2026, 7, 12, 10, 0),
        down_push_sent=False,  # outage tracked but never notified
    )
    t_contact = _local(2026, 7, 12, 10, 5)
    assert _unreachable("p1", t_contact, t_contact, state) is None
    t_stable = t_contact + STABLE_RECOVER_AFTER
    assert _unreachable("p1", t_stable, t_stable, state, dry_run=False) is None
    assert state.incident_since is None  # cleared, but no push


def test_device_unreachable_unconfirmed_stand_is_unknown_no_mode_name():
    state = DeviceUnreachableState()
    last = _local(2026, 7, 12, 10, 0)
    result = _unreachable(
        "battery", last, last + timedelta(minutes=15), state,
        last_known_stand="zelfverbruik", stand_confirmed=False,
    )
    assert result is not None
    assert "Laatst bekende stand: onbekend" in result["body"]
    assert "zelfverbruik" not in result["body"]


def test_device_unreachable_live_and_dry_run_recovery_copy():
    def _recover(dry_run: bool, device: str) -> str:
        state = DeviceUnreachableState()
        last = _local(2026, 7, 12, 10, 0)
        t_down = last + timedelta(minutes=15)
        assert _unreachable(device, last, t_down, state, dry_run=dry_run) is not None
        t_contact = t_down + timedelta(minutes=1)
        assert _unreachable(device, t_contact, t_contact, state, dry_run=dry_run) is None
        up = _unreachable(
            device, t_contact + STABLE_RECOVER_AFTER,
            t_contact + STABLE_RECOVER_AFTER, state, dry_run=dry_run,
        )
        assert up is not None
        return up["body"]

    assert _recover(False, "battery") == "Batterij weer bereikbaar, EMS stuurt weer."
    assert _recover(True, "battery") == "Batterij weer bereikbaar, EMS kijkt weer mee."
    assert _recover(False, "p1") == "P1-meter weer bereikbaar, EMS stuurt weer."
    assert _recover(True, "p1") == "P1-meter weer bereikbaar, EMS kijkt weer mee."


def test_device_unreachable_never_seen_stays_quiet():
    state = DeviceUnreachableState()
    assert _unreachable("battery", None, _local(2026, 7, 12, 12, 0), state) is None


def test_open_incidents_from_notifications_survives_restart_shape():
    since = _local(2026, 7, 12, 10, 15)
    rows = [
        {"key": "battery_down", "dedupe_key": f"battery_down:{since.isoformat()}"},
        {"key": "price_opportunity", "dedupe_key": "price_opp:2026-07-13"},
    ]
    restored = open_incidents_from_notifications(rows)
    assert "battery" in restored
    assert restored["battery"].incident_since == since
    assert restored["battery"].down_push_sent is True

    # Matching up closes it.
    rows.append({"key": "battery_up", "dedupe_key": f"battery_up:{since.isoformat()}"})
    assert open_incidents_from_notifications(rows) == {}
