"""Solar forecast adapter registry + factory (SPEC §6.3 / B-14).

Adapters implement the shared `SolarForecastSource` port (`slots() → list[ForecastSlot]`).
This module is the **only** place that picks which adapter to construct from settings — wiring
(`ems/connection.py`) calls `build_solar_forecast` and never branches on provider names itself.

Register a new provider with `@register_forecast_provider("name")` returning a builder, then add
the matching `solar.forecast_provider` enum option in `ems/settings.py`. Fallback chain (Solcast →
Forecast.Solar → model) stays inside the Solcast adapter / Forecast.Solar adapter, per SPEC.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from ems.sources.forecast import MockSolarForecastSource

_log = logging.getLogger("ems.sources.forecast_factory")

# builder(eff, tz, *, cache_store, fallback) -> SolarForecastSource
ForecastBuilder = Callable[..., Any]

_REGISTRY: dict[str, ForecastBuilder] = {}


def register_forecast_provider(name: str) -> Callable[[ForecastBuilder], ForecastBuilder]:
    """Decorator: register `name` as a `solar.forecast_provider` adapter id."""

    def deco(fn: ForecastBuilder) -> ForecastBuilder:
        key = name.strip().lower()
        if not key:
            raise ValueError("forecast provider name must be non-empty")
        if key in _REGISTRY:
            raise ValueError(f"forecast provider already registered: {key}")
        _REGISTRY[key] = fn
        return fn

    return deco


def registered_providers() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def _site_kw(eff: dict) -> dict[str, float]:
    return {
        "lat": float(eff["site.lat"]),
        "lon": float(eff["site.lon"]),
        "tilt": float(eff["site.tilt"]),
        "azimuth": float(eff["site.azimuth"]),
        "kwp": float(eff["site.kwp"]),
    }


@register_forecast_provider("forecast_solar")
def _build_forecast_solar(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    fallback: object | None = None,
) -> Any:
    from ems.sources.forecast_solar import ForecastSolarSource

    return ForecastSolarSource(tz=tz, cache_store=cache_store, fallback=fallback, **_site_kw(eff))


@register_forecast_provider("solcast")
def _build_solcast(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    fallback: object | None = None,
) -> Any | None:
    """Build Solcast when credentials exist; return None so the factory can fall back."""
    api_key = (eff.get("solar.solcast_api_key") or "").strip()
    resource_id = (eff.get("solar.solcast_resource_id") or "").strip()
    if not api_key or not resource_id:
        _log.warning(
            "solar.forecast_provider=solcast but api key / resource id missing; "
            "using Forecast.Solar"
        )
        return None
    from ems.sources.solcast import SolcastSource

    return SolcastSource(
        tz=tz,
        api_key=api_key,
        resource_id=resource_id,
        daily_budget=int(eff.get("solar.solcast_daily_call_budget") or 10),
        fallback=fallback,
        cache_store=cache_store,
    )


def build_solar_forecast(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    use_live: bool | None = None,
) -> Any:
    """Construct the solar forecast source for the effective settings.

    - Mock curve when live devices / site lat-lon are not available.
    - Otherwise look up `solar.forecast_provider` in the registry (default `forecast_solar`).
    - Solcast always receives Forecast.Solar as its SPEC fallback; unknown providers fall back to
      Forecast.Solar as well (fail safe — never worse than the keyless path).
    """
    live = bool(use_live) if use_live is not None else (
        bool(eff.get("connection.use_live_devices")) and bool(eff.get("meters.p1_ip"))
    )
    if not live or eff.get("site.lat") is None or eff.get("site.lon") is None:
        return MockSolarForecastSource(tz)

    provider = str(eff.get("solar.forecast_provider") or "forecast_solar").strip().lower()
    # Forecast.Solar is always available as the keyless baseline / Solcast fallback.
    baseline = _build_forecast_solar(eff, tz, cache_store=cache_store)
    if provider == "forecast_solar":
        return baseline

    builder = _REGISTRY.get(provider)
    if builder is None:
        _log.warning("unknown solar.forecast_provider=%r; using Forecast.Solar", provider)
        return baseline

    built = builder(eff, tz, cache_store=cache_store, fallback=baseline)
    return built if built is not None else baseline
