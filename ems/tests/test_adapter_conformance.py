"""Hermetic conformance checks for the external source adapters.

These tests deliberately inject every transport.  They exercise the public port methods and the
fail-safe behavior used when an upstream is unavailable; no network request or battery write is
allowed here.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from ems.domain import PhysicalMode
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.forecast_solar import ForecastSolarSource
from ems.sources.indevolt import BatteryUnavailable, IndevoltReadClient
from ems.sources.indevolt_driver import IndevoltBatteryDriver
from ems.sources.live import HomeWizardMeter, ev_w, grid_w, solar_w
from ems.sources.mock import MockSource
from ems.sources.ports import BatteryDriver, PriceSource, SolarForecastSource, Source
from ems.sources.tibber import TibberPriceSource


def test_mock_source_conforms_and_returns_normalized_sample() -> None:
    source = MockSource(total_gas_m3=12.5)
    assert isinstance(source, Source)
    sample = source.read()
    assert sample.grid_power_w == 200.0
    assert sample.soc_pct == 55.0
    assert sample.total_gas_m3 == 12.5


def test_tibber_conforms_and_transport_failure_is_empty() -> None:
    payload = {
        "viewer": {"homes": [{"currentSubscription": {"priceInfo": {"today": [
            {"total": 0.25, "startsAt": "2026-07-29T12:00:00+02:00"},
        ], "tomorrow": []}}}]}
    }
    source = TibberPriceSource("token", http_post=lambda *_: payload)
    assert isinstance(source, PriceSource)
    assert len(source.slots()) == 4

    def unavailable(*_):
        raise OSError("upstream unavailable")

    assert TibberPriceSource("token", http_post=unavailable).slots() == []


def test_forecast_adapters_conform_and_forecast_solar_falls_back() -> None:
    tz = ZoneInfo("Europe/Amsterdam")
    def clock() -> datetime:
        return datetime(2026, 7, 29, 10, tzinfo=UTC)
    mock = MockSolarForecastSource(tz, kwp=2.0, clock=clock, horizon_slots=8)
    assert isinstance(mock, SolarForecastSource)
    assert len(mock.slots()) == 8

    fallback = MockSolarForecastSource(tz, kwp=2.0, clock=clock, horizon_slots=8)
    live = ForecastSolarSource(
        tz=tz, lat=52.0, lon=5.0, tilt=35, azimuth=0, kwp=2.0,
        horizon_slots=8, http_get=lambda _url: (_ for _ in ()).throw(OSError("offline")),
        fallback=fallback, clock=clock,
    )
    assert isinstance(live, SolarForecastSource)
    assert live.slots() == fallback.slots()
    assert live.source_label == "model (fallback)"


def test_homewizard_adapter_normalizes_signs_without_network() -> None:
    meter = HomeWizardMeter("192.0.2.1", http_get=lambda _url: {"active_power_w": -123.4})
    assert meter.read()["active_power_w"] == -123.4
    assert grid_w({"active_power_w": 42}) == 42.0
    assert solar_w({"active_power_w": -500}) == 500.0
    assert solar_w({"active_power_w": 25}) == 0.0
    assert ev_w({"active_power_w": -25}) == 0.0


def test_indevolt_read_conforms_and_failure_is_explicit() -> None:
    client = IndevoltReadClient(
        "192.0.2.2",
        rpc_post=lambda _keys: {"6002": 66, "6000": 800, "6001": 1002},
    )
    assert client.read_power_soc() == (800.0, 66.0)

    broken = IndevoltReadClient("192.0.2.2", rpc_post=lambda _keys: {})
    with pytest.raises(BatteryUnavailable):
        broken.read_power_soc()


def test_indevolt_driver_conforms_but_unarmed_apply_never_writes() -> None:
    calls: list[tuple[int, list[int]]] = []
    driver = IndevoltBatteryDriver(
        "192.0.2.2", armed=False, rpc_post=lambda point, values: calls.append((point, values)),
        reader=IndevoltReadClient(
            "192.0.2.2", rpc_post=lambda _keys: {
                "6002": 66, "6000": 0, "6001": 1000, "7101": 4,
                "142": 10, "7120": 1000,
            },
        ),
    )
    assert isinstance(driver, BatteryDriver)
    assert driver.apply(PhysicalMode.AUTO) is False
    assert calls == []
