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


def _der(hh, load):
    return {"ts": (DAY + timedelta(hours=hh)).isoformat(), "house_load_w": load,
            "non_ev_load_w": load}


def _flows(raw, der, *, partial=False):
    return build_daily_flows(raw, der, DAY, END, label="2026-06-28", partial=partial)


def test_allocate_slot_solar_first_surplus():
    # 3 kW solar, 1 kW house, 1 kW into battery, 1 kW exported (15 min slot).
    sh, sb, sg, gh, gb, bh = _allocate_slot(
        solar_w=3000, grid_w=-1000, battery_w=-1000, load_w=1000)
    assert (sh, sb, sg) == (0.25, 0.25, 0.25)  # home, battery, export
    assert (gh, gb, bh) == (0.0, 0.0, 0.0)  # nothing from the grid, battery not discharging


def test_allocate_slot_grid_charges_battery():
    # Night top-up: no sun, 4 kW into the battery from the grid while the house draws 0.4 kW.
    sh, sb, sg, gh, gb, bh = _allocate_slot(solar_w=0, grid_w=4400, battery_w=-4000, load_w=400)
    assert (sh, sb, sg) == (0.0, 0.0, 0.0)
    assert gb == 1.0  # 4 kW × 0.25 h = 1 kWh of grid-fed charging (the "buying to charge" band)
    assert gh == 0.1 and bh == 0.0


def test_allocate_slot_battery_powers_the_home():
    # Night: battery discharges to fully cover the house, nothing from the grid.
    sh, sb, sg, gh, gb, bh = _allocate_slot(solar_w=0, grid_w=0, battery_w=800, load_w=800)
    assert bh == 0.2 and gh == 0.0 and (sh, sb, sg, gb) == (0.0, 0.0, 0.0, 0.0)


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
    assert f.home_kwh == 0.55
    assert f.self_sufficiency_pct == 81.8


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
    right = f.home_kwh + f.battery_charge_kwh + f.grid_export_kwh
    assert abs(left - right) < 0.01, f"Sankey not balanced: {left} vs {right}"


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
    assert {"solar_to_battery", "grid_to_home", "grid_to_battery", "self_sufficiency_pct"} <= set(b)


def test_endpoint_future_and_bad_date(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_store(db)
    with TestClient(_app(db)) as c:
        future = c.get("/api/energy-distribution?date=2099-01-01").json()
        assert future["has_data"] is False  # no future data, but not an error
        assert c.get("/api/energy-distribution?date=not-a-date").status_code == 422
