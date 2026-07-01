"""Daily energy-distribution flows (the Sankey view): solar-first allocation of each slot's energy
into the six source→sink bands, summed over a calendar day. Pure — canned rows, no hardware."""
import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.energy_flow import _allocate_slot, build_daily_flows
from ems.load_model import DerivedSample
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

DAY = datetime(2026, 6, 28, tzinfo=UTC)  # a full UTC day
END = DAY + timedelta(days=1)


def _raw(hh, *, grid, solar, batt):
    return {"ts": (DAY + timedelta(hours=hh)).isoformat(), "grid_power_w": grid,
            "solar_power_w": solar, "battery_power_w": batt, "ev_power_w": 0.0, "soc_pct": 50.0}


def _der(hh, load, non_ev=None):
    # house_load_w = total demand (incl. car); non_ev_load_w = house-only. car = total − non_ev.
    return {"ts": (DAY + timedelta(hours=hh)).isoformat(), "house_load_w": load,
            "non_ev_load_w": load if non_ev is None else non_ev}


def _flows(raw, der, *, partial=False):
    return build_daily_flows(raw, der, DAY, END, label="2026-06-28", partial=partial)


def test_allocate_slot_solar_first_surplus():
    # 3 kW solar, 1 kW house, 1 kW into battery, 1 kW exported (15 min slot). No car.
    r = _allocate_slot(solar_w=3000, grid_w=-1000, battery_w=-1000, home_w=1000)
    assert (r.solar_home, r.solar_batt, r.solar_grid) == (0.25, 0.25, 0.25)
    assert (r.grid_home, r.grid_batt, r.batt_home) == (0.0, 0.0, 0.0)
    assert (r.solar_car, r.grid_car, r.batt_car) == (0.0, 0.0, 0.0)  # no car this slot


def test_allocate_slot_grid_charges_battery():
    # Night top-up: no sun, 4 kW into the battery from the grid while the house draws 0.4 kW.
    r = _allocate_slot(solar_w=0, grid_w=4400, battery_w=-4000, home_w=400)
    assert (r.solar_home, r.solar_batt, r.solar_grid) == (0.0, 0.0, 0.0)
    assert r.grid_batt == 1.0  # 4 kW × 0.25 h = 1 kWh grid-fed charging ("buying to charge")
    assert r.grid_home == 0.1 and r.batt_home == 0.0


def test_allocate_slot_battery_powers_the_home():
    # Night: battery discharges to fully cover the house, nothing from the grid.
    r = _allocate_slot(solar_w=0, grid_w=0, battery_w=800, home_w=800)
    assert r.batt_home == 0.2 and r.grid_home == 0.0
    assert (r.solar_home, r.solar_batt, r.solar_grid, r.grid_batt) == (0.0, 0.0, 0.0, 0.0)


def test_allocate_slot_solar_and_grid_feed_the_car():
    # Midday: 3 kW solar, 0.5 kW house, 4 kW car, battery idle. Solar serves home first, then the
    # car; the grid covers the rest of the car. Battery never feeds the car here.
    r = _allocate_slot(solar_w=3000, grid_w=1500, battery_w=0, home_w=500, car_w=4000)
    assert r.solar_home == 0.125 and r.solar_car == 0.625  # 0.75 kWh solar: 0.125 home, 0.625 car
    assert r.grid_car == 0.375 and r.batt_car == 0.0        # grid tops the car up; no battery leak
    assert r.solar_grid == 0.0


def test_allocate_slot_battery_leak_into_car_is_flagged():
    # Car-guard FAILURE: battery discharges 2 kW while the car pulls 3 kW and the house needs only
    # 0.4 kW → 0.1 kWh to home, then 0.4 kWh LEAKS into the car (batt_car), grid covers the rest.
    r = _allocate_slot(solar_w=0, grid_w=1400, battery_w=2000, home_w=400, car_w=3000)
    assert r.batt_home == 0.1
    assert r.batt_car == 0.4   # the leak — this is the car-guard diagnostic
    assert r.grid_car == 0.35


def test_daily_flows_sum_and_self_sufficiency():
    # A day: a midday solar surplus slot + a night battery slot + a night grid-charge slot.
    raw = [
        _raw(12, grid=-1000, solar=3000, batt=-1000),  # solar surplus
        _raw(22, grid=0, solar=0, batt=800),           # battery powers the home
        _raw(3, grid=4400, solar=0, batt=-4000),       # grid charges the battery
    ]
    der = [_der(12, 1000), _der(22, 800), _der(3, 400)]
    f = _flows(raw, der)
    assert f.has_data is True
    assert f.solar_to_home == 0.25 and f.solar_to_battery == 0.25 and f.solar_to_grid == 0.25
    assert f.battery_to_home == 0.2
    assert f.grid_to_battery == 1.0 and f.grid_to_home == 0.1
    # Node totals.
    assert f.solar_kwh == 0.75 and f.grid_export_kwh == 0.25
    assert f.grid_import_kwh == 1.1 and f.battery_charge_kwh == 1.25
    # Home = 0.25 (solar) + 0.2 (battery) + 0.1 (grid) = 0.55; self-served = 0.45 → 81.8%.
    assert f.home_kwh == 0.55 and f.car_kwh == 0.0
    assert f.self_sufficiency_pct == 81.8
    # Solar used on-site = 0.25 home + 0.25 battery = 0.5 of 0.75 produced → 66.7%.
    assert f.solar_self_consumption_pct == 66.7
    assert f.car_guard_leak_kwh == 0.0


def test_flows_conserve_energy():
    # Every allocated kWh has a source AND a sink, so the Sankey balances: sources (solar + grid
    # import + battery discharge) == sinks (home + battery charge + export). Mixed slots.
    raw = [
        _raw(8, grid=200, solar=1500, batt=-800),
        _raw(13, grid=-2000, solar=3500, batt=-1500),
        _raw(19, grid=300, solar=200, batt=1200),
        _raw(2, grid=4000, solar=0, batt=-3600),
    ]
    der = [_der(8, 900), _der(13, 1500), _der(19, 1500), _der(2, 400)]
    f = _flows(raw, der)
    left = f.solar_kwh + f.grid_import_kwh + f.battery_discharge_kwh
    right = f.home_kwh + f.car_kwh + f.battery_charge_kwh + f.grid_export_kwh
    assert abs(left - right) < 0.01, f"Sankey not balanced: {left} vs {right}"


def test_daily_flows_with_a_charging_car():
    # Midday solar while the car charges: solar covers home (1 kW) then the car (8 kW), grid tops
    # the car up. 15 min → home 0.25 kWh, car 2.0 kWh, solar 1.0 kWh (0.25 home + 0.75 car).
    raw = [_raw(12, grid=5000, solar=4000, batt=0)]
    der = [_der(12, 9000, non_ev=1000)]  # 9 kW total = 1 kW house + 8 kW car
    f = _flows(raw, der)
    assert f.home_kwh == 0.25 and f.car_kwh == 2.0
    assert f.solar_to_home == 0.25 and f.solar_to_car == 0.75
    assert f.grid_to_car == 1.25 and f.car_guard_leak_kwh == 0.0
    assert f.solar_self_consumption_pct == 100.0  # all 1.0 kWh solar used on-site


def test_flows_backward_compat_without_non_ev_column():
    # Old derived rows (before non_ev_load_w existed) → all load is treated as home, car = 0.
    raw = [_raw(12, grid=-1000, solar=3000, batt=0)]
    der = [{"ts": (DAY + timedelta(hours=12)).isoformat(), "house_load_w": 1000}]  # no non_ev
    f = _flows(raw, der)
    assert f.car_kwh == 0.0 and f.home_kwh == 0.25


def test_empty_day_is_graceful():
    f = _flows([], [])
    assert f.has_data is False
    assert f.solar_kwh == 0.0 and f.home_kwh == 0.0
    assert f.self_sufficiency_pct is None


def _seed_store(db_path: str) -> None:
    async def go():
        store = HistoryStore(db_path)
        await store.init()
        for hh, (g, s, b, load) in {
            12: (-1000, 3000, -1000, 1000),
            22: (0, 0, 800, 800),
        }.items():
            ts = (DAY + timedelta(hours=hh)).isoformat()
            await store.record(ts, RawSample(g, s, b, 0.0, 50.0), DerivedSample(load, load))
    asyncio.run(go())


def _app(db_path):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=UTC,
        store=HistoryStore(db_path), settings_store=SettingsStore(db_path),
    )


def test_endpoint_returns_a_days_distribution(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_store(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/energy-distribution?date=2026-06-28").json()
    assert b["date"] == "2026-06-28" and b["has_data"] is True
    assert b["solar_to_home"] == 0.25 and b["battery_to_home"] == 0.2
    assert {"solar_to_battery", "grid_to_home", "grid_to_battery", "self_sufficiency_pct",
            "solar_to_car", "grid_to_car", "battery_to_car", "car_kwh",
            "solar_self_consumption_pct", "car_guard_leak_kwh"} <= set(b)


def test_endpoint_future_and_bad_date(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_store(db)
    with TestClient(_app(db)) as c:
        future = c.get("/api/energy-distribution?date=2099-01-01").json()
        assert future["has_data"] is False  # no future data, but not an error
        assert c.get("/api/energy-distribution?date=not-a-date").status_code == 422
