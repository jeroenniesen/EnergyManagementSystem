"""Price adapter factory / registry (#114 slice a)."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from ems.settings import effective_settings
from ems.sources.price_factory import (
    PRICE_DOMAIN,
    build_price_source,
    register_price_provider,
    registered_providers,
)
from ems.sources.prices import MockPriceSource
from ems.sources.registry import get_metadata, registered_adapters
from ems.sources.tibber import TibberPriceSource

AMS = ZoneInfo("Europe/Amsterdam")


def test_registry_includes_mock_and_tibber():
    assert "mock" in registered_providers()
    assert "tibber" in registered_providers()
    assert registered_adapters(PRICE_DOMAIN) == registered_providers()
    assert get_metadata(PRICE_DOMAIN, "mock") is not None
    assert get_metadata(PRICE_DOMAIN, "tibber") is not None


def test_register_price_provider_duplicate_fails():
    with pytest.raises(ValueError, match="already registered"):
        @register_price_provider("mock")
        def _dup():  # pragma: no cover
            return None


def test_build_mock_when_live_prices_off():
    src = build_price_source(effective_settings({}), AMS)
    assert isinstance(src, MockPriceSource)


def test_build_tibber_when_live_prices_configured():
    eff = effective_settings({
        "connection.use_live_prices": True,
        "prices.tibber_token": "tok",
    })
    src = build_price_source(eff, AMS, use_live=True)
    assert isinstance(src, TibberPriceSource)


def test_tibber_without_token_falls_back_to_mock():
    # Force the tibber builder path with use_live=True but empty token.
    raw = dict(effective_settings({"connection.use_live_prices": True}))
    raw["prices.tibber_token"] = ""
    src = build_price_source(raw, AMS, use_live=True)
    assert isinstance(src, MockPriceSource)
