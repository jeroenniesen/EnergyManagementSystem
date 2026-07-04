"""Reconstruct the last-24h energy story from recorded history: resample to 15-min slots, integrate
kWh, split import/export and charge/discharge, cost it against prices. Pure — canned rows."""
from datetime import UTC, datetime, timedelta

from ems.retrospect import build_past_story
from ems.sources.prices import PriceSlot

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _raw(ts: datetime, *, grid=0.0, solar=0.0, batt=0.0, soc=50.0) -> dict:
    return {"ts": ts.isoformat(), "grid_power_w": grid, "solar_power_w": solar,
            "battery_power_w": batt, "ev_power_w": 0.0, "soc_pct": soc}


def _der(ts: datetime, load=0.0) -> dict:
    return {"ts": ts.isoformat(), "house_load_w": load, "non_ev_load_w": load}


def test_non_ev_load_carried_separately_for_the_charge_split():
    # Bug: when the CAR is charging, house_load includes the EV, so "solar left after the house"
    # went to zero and a solar-fed battery charge read as a grid charge. The past slot must carry
    # non_ev (house-only) load ALONGSIDE total load, so the charge-kind split can use it.
    # Balanced slot: grid 1600 + solar 3500 = house 300 + car 4000 + battery charge 800.
    t = NOW - timedelta(minutes=30)
    raw = [_raw(t, grid=1600.0, solar=3500.0, batt=-800.0)]
    der = [{"ts": t.isoformat(), "house_load_w": 4300.0, "non_ev_load_w": 300.0}]
    s = build_past_story(raw, der, [], NOW)
    assert len(s.slots) == 1
    assert s.slots[0].load_w == 4300.0        # total house load (incl. car) — still plotted
    assert s.slots[0].non_ev_load_w == 300.0  # house-only — the correct charge-split input


def test_empty_history_is_an_empty_story():
    s = build_past_story([], [], [], NOW)
    assert s.slots == []
    assert s.import_kwh == 0.0 and s.export_kwh == 0.0 and s.solar_kwh == 0.0
    assert s.grid_cost_eur is None
    assert s.self_sufficiency_pct is None
    assert s.soc_start_pct is None and s.soc_end_pct is None


def test_samples_in_one_slot_average_and_integrate():
    # Two samples in the same 15-min slot, 2 kW grid import -> 2000 W mean * 0.25 h = 0.5 kWh.
    t = NOW - timedelta(minutes=50)  # 11:10 -> slot 11:00
    raw = [_raw(t, grid=2000.0, soc=60.0), _raw(t + timedelta(minutes=2), grid=2000.0, soc=58.0)]
    s = build_past_story(raw, [], [], NOW)
    assert len(s.slots) == 1
    assert s.import_kwh == 0.5
    assert s.export_kwh == 0.0
    assert s.slots[0].soc_pct == 59.0  # averaged


def test_import_export_split_by_grid_sign():
    a = NOW - timedelta(minutes=40)
    b = NOW - timedelta(minutes=25)
    raw = [_raw(a, grid=4000.0), _raw(b, grid=-4000.0)]  # import slot, then export slot
    s = build_past_story(raw, [], [], NOW)
    assert s.import_kwh == 1.0  # 4000 W * 0.25 h
    assert s.export_kwh == 1.0


def test_charge_discharge_split_by_battery_sign():
    a = NOW - timedelta(minutes=40)
    b = NOW - timedelta(minutes=25)
    raw = [_raw(a, batt=-4000.0), _raw(b, batt=2000.0)]  # charging then discharging
    s = build_past_story(raw, [], [], NOW)
    assert s.charge_kwh == 1.0
    assert s.discharge_kwh == 0.5


def test_grid_cost_uses_aligned_prices():
    # One import slot of 0.5 kWh at €0.20 -> €0.10.
    t = (NOW - timedelta(minutes=40)).replace(minute=15)  # land on a quarter-hour
    slot = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
    raw = [_raw(slot + timedelta(minutes=3), grid=2000.0)]
    prices = [PriceSlot(slot, 0.20)]
    s = build_past_story(raw, [], prices, NOW)
    assert s.import_kwh == 0.5
    assert s.grid_cost_eur == 0.10


def test_self_sufficiency_from_load_and_import():
    # House used 2 kWh, imported 0.5 kWh -> 75% served without the grid.
    a = NOW - timedelta(minutes=40)
    raw = [_raw(a, grid=2000.0)]
    der = [_der(a, load=8000.0)]  # 8000 W * 0.25 h = 2 kWh
    s = build_past_story(raw, der, [], NOW)
    assert s.load_kwh == 2.0
    assert s.import_kwh == 0.5
    assert s.self_sufficiency_pct == 75.0


def test_window_excludes_old_samples():
    old = NOW - timedelta(hours=30)  # outside the 24h window
    recent = NOW - timedelta(minutes=20)
    raw = [_raw(old, grid=9999.0), _raw(recent, grid=4000.0)]
    s = build_past_story(raw, [], [], NOW, hours=24)
    assert len(s.slots) == 1  # only the recent slot
    assert s.import_kwh == 1.0


def test_soc_start_and_end_track_first_and_last_slot():
    a = NOW - timedelta(minutes=60)
    b = NOW - timedelta(minutes=10)
    raw = [_raw(a, soc=90.0), _raw(b, soc=40.0)]
    s = build_past_story(raw, [], [], NOW)
    assert s.soc_start_pct == 90.0
    assert s.soc_end_pct == 40.0


def test_naive_timestamps_treated_as_utc():
    t = (NOW - timedelta(minutes=20)).replace(tzinfo=None)  # naive
    raw = [{"ts": t.isoformat(), "grid_power_w": 4000.0, "solar_power_w": 0.0,
            "battery_power_w": 0.0, "ev_power_w": 0.0, "soc_pct": 50.0}]
    s = build_past_story(raw, [], [], NOW)
    assert len(s.slots) == 1 and s.import_kwh == 1.0
