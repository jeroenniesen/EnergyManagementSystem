"""Behaviour contract suite for price + forecast adapters (#114 slice a).

Parametrized over every adapter registered under ``price`` / ``forecast`` (post-#140 registry).
Hermetic: every transport is injected; no network and no battery writes.

Battery / meter / CO₂ ports are out of scope for this slice (see #114 b/c).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

# Side-effect imports: register production adapters before we read the registry.
import ems.sources.forecast_factory as forecast_factory  # noqa: F401
import ems.sources.price_factory as price_factory  # noqa: F401
from ems.sources.forecast import SLOT as FORECAST_SLOT
from ems.sources.forecast import ForecastSlot, MockSolarForecastSource
from ems.sources.forecast_factory import FORECAST_DOMAIN
from ems.sources.ports import PriceSource, SolarForecastSource
from ems.sources.price_factory import PRICE_DOMAIN
from ems.sources.prices import SLOT as PRICE_SLOT
from ems.sources.prices import PriceSlot
from ems.sources.registry import get_builder, get_metadata, registered_adapters

AMS = ZoneInfo("Europe/Amsterdam")
# Midsummer local noon → UTC (CEST = UTC+2).
FIXED_NOW = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED_NOW


def _site_eff() -> dict:
    return {
        "site.lat": 52.0,
        "site.lon": 5.0,
        "site.tilt": 35.0,
        "site.azimuth": 0.0,
        "site.kwp": 2.0,
        "prices.tibber_token": "contract-tok",
        "solar.solcast_api_key": "contract-tok",
        "solar.solcast_resource_id": "contract-rid",
    }


# --- Price fixtures ---------------------------------------------------------

_TIBBER_OK = {
    "viewer": {
        "homes": [{
            "currentSubscription": {
                "priceInfo": {
                    "today": [
                        {"total": 0.25, "startsAt": "2026-07-29T12:00:00+02:00"},
                        {"total": 0.30, "startsAt": "2026-07-29T13:00:00+02:00"},
                    ],
                    "tomorrow": [],
                }
            }
        }]
    }
}


def _build_price(name: str, *, http_post=None, horizon_slots: int | None = 16):
    builder = get_builder(PRICE_DOMAIN, name)
    assert builder is not None, f"price adapter {name!r} missing from registry"
    return builder(
        _site_eff(),
        AMS,
        clock=_clock,
        http_post=http_post,
        horizon_slots=horizon_slots,
    )


def _assert_price_slots(slots: list[PriceSlot]) -> None:
    assert slots, "price adapter must return at least one slot on the happy path"
    for slot in slots:
        assert isinstance(slot, PriceSlot)
        assert slot.start.tzinfo is not None, "price slots must be tz-aware"
        assert isinstance(slot.eur_per_kwh, (int, float))
    starts = [s.start for s in slots]
    assert starts == sorted(starts), "price slots must be chronological"
    if len(starts) >= 2:
        deltas = {b - a for a, b in zip(starts, starts[1:], strict=False)}
        assert deltas == {PRICE_SLOT}, f"expected 15-min spacing, got {deltas}"


# --- Forecast fixtures ------------------------------------------------------

_FS_OK = {
    "result": {
        "watts": {
            "2026-07-29 12:00:00": 800.0,
            "2026-07-29 13:00:00": 1200.0,
            "2026-07-29 14:00:00": 900.0,
        }
    }
}

_SOLCAST_OK = {
    "forecasts": [
        {
            "pv_estimate": 0.8,
            "pv_estimate10": 0.5,
            "pv_estimate90": 1.1,
            "period_end": "2026-07-29T12:30:00.0000000Z",
            "period": "PT30M",
        },
        {
            "pv_estimate": 1.2,
            "pv_estimate10": 0.7,
            "pv_estimate90": 1.6,
            "period_end": "2026-07-29T13:00:00.0000000Z",
            "period": "PT30M",
        },
    ]
}


def _offline(*_args, **_kwargs):
    raise OSError("upstream unavailable")


def _build_forecast(name: str, *, http_get=None, horizon_slots: int = 8):
    builder = get_builder(FORECAST_DOMAIN, name)
    assert builder is not None, f"forecast adapter {name!r} missing from registry"
    fallback = MockSolarForecastSource(
        AMS, kwp=2.0, clock=_clock, horizon_slots=horizon_slots,
    )
    return builder(
        _site_eff(),
        AMS,
        clock=_clock,
        http_get=http_get,
        horizon_slots=horizon_slots,
        fallback=fallback,
    )


def _happy_http_get(name: str):
    """Return a canned transport matching the adapter's http_get arity."""
    if name == "solcast":
        return lambda _url, _headers: _SOLCAST_OK
    if name == "forecast_solar":
        return lambda _url: _FS_OK
    return None  # mock needs no transport


def _assert_forecast_bands(slots: list[ForecastSlot]) -> None:
    assert slots, "forecast adapter must return at least one slot on the happy path"
    for slot in slots:
        assert isinstance(slot, ForecastSlot)
        assert slot.start.tzinfo is not None, "forecast slots must be tz-aware"
        assert slot.p10_w <= slot.p50_w <= slot.p90_w, (
            f"P10≤P50≤P90 violated at {slot.start}: "
            f"{slot.p10_w} / {slot.p50_w} / {slot.p90_w}"
        )
        assert slot.p10_w >= 0.0 and slot.p90_w >= 0.0
    starts = [s.start for s in slots]
    assert starts == sorted(starts)
    if len(starts) >= 2:
        deltas = {b - a for a, b in zip(starts, starts[1:], strict=False)}
        assert deltas == {FORECAST_SLOT}, f"expected 15-min spacing, got {deltas}"


# --- Registry presence ------------------------------------------------------

def test_price_domain_registers_mock_and_tibber():
    names = registered_adapters(PRICE_DOMAIN)
    assert "mock" in names
    assert "tibber" in names
    assert get_metadata(PRICE_DOMAIN, "mock") is not None
    assert get_metadata(PRICE_DOMAIN, "tibber") is not None


def test_forecast_domain_registers_mock_and_live_providers():
    names = registered_adapters(FORECAST_DOMAIN)
    assert "mock" in names
    assert "forecast_solar" in names
    assert "solcast" in names


# --- Price contracts (parametrized from registry) ---------------------------

@pytest.mark.parametrize("name", registered_adapters(PRICE_DOMAIN))
def test_price_adapter_conforms_and_returns_tz_aware_15min_slots(name: str):
    http_post = (lambda *_: _TIBBER_OK) if name == "tibber" else None
    src = _build_price(name, http_post=http_post)
    assert isinstance(src, PriceSource)
    _assert_price_slots(src.slots())


@pytest.mark.parametrize("name", registered_adapters(PRICE_DOMAIN))
def test_price_adapter_failure_is_empty_or_last_good(name: str):
    """On transport failure: [] when cold, or last-good when a prior fetch succeeded.

    Synthetic adapters (mock) have no network path — they keep returning a curve.
    """
    meta = get_metadata(PRICE_DOMAIN, name)
    caps = meta.capabilities if meta is not None else frozenset()

    if "network" not in caps:
        # Mock / synthetic: no failure transport; still must not raise and must stay tz-aware.
        slots = _build_price(name).slots()
        _assert_price_slots(slots)
        return

    # Cold failure → empty (no last-good yet).
    cold = _build_price(name, http_post=_offline)
    assert cold.slots() == []

    # Seed last-good, then fail — must keep serving the cached curve (fail safe).
    calls = {"n": 0}

    def flaky(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _TIBBER_OK
        raise OSError("upstream unavailable")

    # Force refetch between calls by collapsing cache TTL via a advancing clock.
    ticks = {"t": FIXED_NOW}

    def advancing_clock() -> datetime:
        return ticks["t"]

    builder = get_builder(PRICE_DOMAIN, name)
    src = builder(
        _site_eff(),
        AMS,
        clock=advancing_clock,
        http_post=flaky,
        cache_ttl=timedelta(seconds=1),
        retry_ttl=timedelta(seconds=1),
    )
    first = src.slots()
    _assert_price_slots(first)
    ticks["t"] = FIXED_NOW + timedelta(minutes=2)
    second = src.slots()
    assert second == first, f"{name}: expected last-good after outage, got {second!r}"


# --- Forecast contracts (parametrized from registry) ------------------------

@pytest.mark.parametrize("name", registered_adapters(FORECAST_DOMAIN))
def test_forecast_adapter_conforms_and_keeps_p10_le_p50_le_p90(name: str):
    src = _build_forecast(name, http_get=_happy_http_get(name))
    assert isinstance(src, SolarForecastSource)
    _assert_forecast_bands(src.slots())


@pytest.mark.parametrize("name", registered_adapters(FORECAST_DOMAIN))
def test_forecast_adapter_failure_falls_back_safely(name: str):
    """Network adapters must fall back (model / injected) — never raise into the control loop.

    Synthetic mock has no network path; it still returns ordered bands.
    """
    meta = get_metadata(FORECAST_DOMAIN, name)
    caps = meta.capabilities if meta is not None else frozenset()

    if "fallback_on_failure" not in caps:
        _assert_forecast_bands(_build_forecast(name).slots())
        return

    src = _build_forecast(name, http_get=_offline)
    slots = src.slots()
    _assert_forecast_bands(slots)
    # Live adapters label the fallback so the UI can say "estimated".
    label = getattr(src, "source_label", "")
    assert "fallback" in str(label).lower() or name == "mock"
