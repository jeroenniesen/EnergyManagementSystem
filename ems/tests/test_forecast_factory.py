"""Solar forecast adapter registry / factory (B-14 / #140)."""
from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

import pytest

from ems.settings import defaults, effective_settings
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.forecast_factory import (
    FORECAST_DOMAIN,
    build_solar_forecast,
    register_forecast_provider,
    registered_providers,
)
from ems.sources.forecast_solar import ForecastSolarSource
from ems.sources.registry import get_metadata, registered_adapters
from ems.sources.solcast import SolcastSource
from ems.storage.settings import SettingsStore

AMS = ZoneInfo("Europe/Amsterdam")

_LIVE = {
    "connection.use_live_devices": True,
    "meters.p1_ip": "192.0.2.10",
    "site.lat": 52.13,
    "site.lon": 5.29,
}


def test_registry_includes_forecast_solar_and_solcast():
    assert "forecast_solar" in registered_providers()
    assert "solcast" in registered_providers()
    # Delegates to the generic registry under domain="forecast".
    assert registered_adapters(FORECAST_DOMAIN) == registered_providers()
    assert get_metadata(FORECAST_DOMAIN, "forecast_solar") is not None
    assert get_metadata(FORECAST_DOMAIN, "solcast") is not None


def test_register_forecast_provider_duplicate_fails():
    with pytest.raises(ValueError, match="already registered"):
        @register_forecast_provider("forecast_solar")
        def _dup():  # pragma: no cover
            return None


def test_build_mock_when_not_live():
    fc = build_solar_forecast(effective_settings({}), AMS)
    assert isinstance(fc, MockSolarForecastSource)


def test_build_forecast_solar_by_default_when_live():
    fc = build_solar_forecast(effective_settings(_LIVE), AMS, use_live=True)
    assert isinstance(fc, ForecastSolarSource)


def test_build_solcast_when_configured():
    eff = effective_settings({
        **_LIVE,
        "solar.forecast_provider": "solcast",
        "solar.solcast_api_key": "tok",
        "solar.solcast_resource_id": "rid-1",
    })
    fc = build_solar_forecast(eff, AMS, use_live=True)
    assert isinstance(fc, SolcastSource)
    assert isinstance(fc._fallback, ForecastSolarSource)


def test_solcast_without_creds_falls_back_to_forecast_solar():
    eff = effective_settings({
        **_LIVE,
        "solar.forecast_provider": "solcast",
    })
    fc = build_solar_forecast(eff, AMS, use_live=True)
    assert isinstance(fc, ForecastSolarSource)


def test_unknown_provider_falls_back_to_forecast_solar():
    # Bypass validation by mutating after effective_settings.
    raw = dict(effective_settings(_LIVE))
    raw["solar.forecast_provider"] = "open_meteo_not_registered"
    fc = build_solar_forecast(raw, AMS, use_live=True)
    assert isinstance(fc, ForecastSolarSource)


def test_ledger_source_class_names_unchanged():
    """sense._persist_forecast stores type(...).__name__ — renaming breaks history (#140)."""
    mock = build_solar_forecast(effective_settings({}), AMS)
    assert type(mock).__name__ == "MockSolarForecastSource"

    fs = build_solar_forecast(effective_settings(_LIVE), AMS, use_live=True)
    assert type(fs).__name__ == "ForecastSolarSource"

    solcast = build_solar_forecast(effective_settings({
        **_LIVE,
        "solar.forecast_provider": "solcast",
        "solar.solcast_api_key": "tok",
        "solar.solcast_resource_id": "rid-1",
    }), AMS, use_live=True)
    assert type(solcast).__name__ == "SolcastSource"


def test_preexisting_settings_db_keeps_forecast_provider(tmp_path):
    """Installations from before #140 start with the same provider — no settings migration."""
    db = str(tmp_path / "pre_registry.sqlite")

    async def seed_and_read():
        store = SettingsStore(db)
        await store.init()
        # Shape a pre-#140 settings row: only the operator-chosen provider (+ live site).
        await store.set_many({
            "solar.forecast_provider": "solcast",
            "solar.solcast_api_key": "legacy-tok",
            "solar.solcast_resource_id": "legacy-rid",
            "connection.use_live_devices": True,
            "meters.p1_ip": "192.0.2.10",
            "site.lat": 52.13,
            "site.lon": 5.29,
        })
        return await store.all()

    stored = asyncio.run(seed_and_read())
    assert stored["solar.forecast_provider"] == "solcast"
    # Defaults still supply forecast_solar when the key is absent.
    assert defaults()["solar.forecast_provider"] == "forecast_solar"

    eff = effective_settings(stored)
    assert eff["solar.forecast_provider"] == "solcast"
    fc = build_solar_forecast(eff, AMS, use_live=True)
    assert type(fc).__name__ == "SolcastSource"


def test_unchosen_forecast_builder_stays_lazy(monkeypatch):
    """Choosing forecast_solar must not import / construct Solcast (#140 lazy builders)."""
    import ems.sources.forecast_factory as ff

    calls: list[str] = []
    real_solcast = ff._build_solcast

    def spy_solcast(*args, **kwargs):
        calls.append("solcast")
        return real_solcast(*args, **kwargs)

    monkeypatch.setattr(ff, "_build_solcast", spy_solcast)
    # Registry still holds the original builder registered at import — re-bind the entry.
    from ems.sources import registry as reg

    entry = reg.get_adapter(FORECAST_DOMAIN, "solcast")
    assert entry is not None
    monkeypatch.setitem(
        reg._REGISTRY[FORECAST_DOMAIN],
        "solcast",
        reg.AdapterEntry(builder=spy_solcast, metadata=entry.metadata),
    )

    fc = build_solar_forecast(effective_settings(_LIVE), AMS, use_live=True)
    assert isinstance(fc, ForecastSolarSource)
    assert calls == []
