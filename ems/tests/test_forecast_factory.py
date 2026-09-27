"""Solar forecast adapter registry / factory (B-14)."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from ems.settings import effective_settings
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.forecast_factory import build_solar_forecast, registered_providers
from ems.sources.forecast_solar import ForecastSolarSource
from ems.sources.solcast import SolcastSource

AMS = ZoneInfo("Europe/Amsterdam")


def test_registry_includes_forecast_solar_and_solcast():
    assert "forecast_solar" in registered_providers()
    assert "solcast" in registered_providers()


def test_build_mock_when_not_live():
    fc = build_solar_forecast(effective_settings({}), AMS)
    assert isinstance(fc, MockSolarForecastSource)


def test_build_forecast_solar_by_default_when_live():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "site.lat": 52.13,
        "site.lon": 5.29,
    })
    fc = build_solar_forecast(eff, AMS, use_live=True)
    assert isinstance(fc, ForecastSolarSource)


def test_build_solcast_when_configured():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "site.lat": 52.13,
        "site.lon": 5.29,
        "solar.forecast_provider": "solcast",
        "solar.solcast_api_key": "tok",
        "solar.solcast_resource_id": "rid-1",
    })
    fc = build_solar_forecast(eff, AMS, use_live=True)
    assert isinstance(fc, SolcastSource)
    assert isinstance(fc._fallback, ForecastSolarSource)


def test_solcast_without_creds_falls_back_to_forecast_solar():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "site.lat": 52.13,
        "site.lon": 5.29,
        "solar.forecast_provider": "solcast",
    })
    fc = build_solar_forecast(eff, AMS, use_live=True)
    assert isinstance(fc, ForecastSolarSource)


def test_unknown_provider_falls_back_to_forecast_solar():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "site.lat": 52.13,
        "site.lon": 5.29,
        "solar.forecast_provider": "forecast_solar",
    })
    # Bypass validation by mutating after effective_settings.
    raw = dict(eff)
    raw["solar.forecast_provider"] = "open_meteo_not_registered"
    fc = build_solar_forecast(raw, AMS, use_live=True)
    assert isinstance(fc, ForecastSolarSource)
