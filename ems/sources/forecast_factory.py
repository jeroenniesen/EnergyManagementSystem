"""Solar forecast adapter factory (SPEC §6.3 / B-14) — first user of the generic registry (#140).

Adapters implement the shared `SolarForecastSource` port (`slots() → list[ForecastSlot]`).
This module is the **only** place that picks which adapter to construct from settings — wiring
(`ems/connection.py`) calls `build_solar_forecast` and never branches on provider names itself.

Register a new provider with `@register_forecast_provider("name")` (delegates to
`ems.sources.registry.register_adapter` under `domain="forecast"`), then add the matching
`solar.forecast_provider` enum option in `ems/settings.py`. Fallback chain (Solcast →
Forecast.Solar → model) stays inside the Solcast adapter / Forecast.Solar adapter, per SPEC.

Registered names: `mock` (credential-free model, #114), `forecast_solar`, `solcast`.

Class names (`ForecastSolarSource`, `SolcastSource`, `MockSolarForecastSource`) are part of the
prediction-ledger provenance (`sense._persist_forecast` stores `type(...).__name__`) — do not
rename them lightly.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ems.sources.forecast import MockSolarForecastSource
from ems.sources.registry import (
    AdapterConfigField,
    AdapterMetadata,
    get_builder,
    register_adapter,
    registered_adapters,
)

_log = logging.getLogger("ems.sources.forecast_factory")

# Domain key in the generic registry. Stable — settings and docs refer to forecast providers by
# adapter *name* (`forecast_solar` / `solcast`), not by this domain string.
FORECAST_DOMAIN = "forecast"

# builder(eff, tz, *, cache_store, fallback, http_get, clock, ...) -> SolarForecastSource | None
ForecastBuilder = Callable[..., Any]


def register_forecast_provider(
    name: str,
    *,
    metadata: AdapterMetadata | None = None,
) -> Callable[[ForecastBuilder], ForecastBuilder]:
    """Decorator: register `name` as a `solar.forecast_provider` adapter id.

    Delegates to the generic `register_adapter` so forecast shares the same registry as future
    domains (CO₂, price, …). The builder is stored lazily — vendor imports run only when chosen.
    """

    def deco(fn: ForecastBuilder) -> ForecastBuilder:
        meta = metadata or AdapterMetadata(label=name.strip().lower() or name)
        register_adapter(FORECAST_DOMAIN, name, fn, meta)
        return fn

    return deco


def registered_providers() -> tuple[str, ...]:
    return registered_adapters(FORECAST_DOMAIN)


def _site_kw(eff: dict) -> dict[str, float]:
    return {
        "lat": float(eff["site.lat"]),
        "lon": float(eff["site.lon"]),
        "tilt": float(eff["site.tilt"]),
        "azimuth": float(eff["site.azimuth"]),
        "kwp": float(eff["site.kwp"]),
    }


def _optional_transport_kw(
    *,
    http_get: object | None = None,
    clock: Callable[[], datetime] | None = None,
    horizon_slots: int | None = None,
) -> dict[str, Any]:
    """Forward injectable clocks / transports for hermetic contract tests (#114)."""
    kwargs: dict[str, Any] = {}
    if http_get is not None:
        kwargs["http_get"] = http_get
    if clock is not None:
        kwargs["clock"] = clock
    if horizon_slots is not None:
        kwargs["horizon_slots"] = horizon_slots
    return kwargs


@register_forecast_provider(
    "mock",
    metadata=AdapterMetadata(
        label="Built-in model",
        description="Credential-free daylight bell curve for mock/dev (SPEC §6.3).",
        capabilities=frozenset({"p10", "p50", "p90", "synthetic"}),
    ),
)
def _build_mock(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    fallback: object | None = None,
    http_get: object | None = None,
    clock: Callable[[], datetime] | None = None,
    horizon_slots: int | None = None,
    **_: Any,
) -> MockSolarForecastSource:
    del cache_store, fallback, http_get  # unused — mock needs no network / fallback
    kwargs: dict[str, Any] = {}
    if clock is not None:
        kwargs["clock"] = clock
    if horizon_slots is not None:
        kwargs["horizon_slots"] = horizon_slots
    # Prefer site.* when present so the mock tracks the same array as live adapters.
    if eff.get("site.kwp") is not None:
        kwargs["kwp"] = float(eff["site.kwp"])
    if eff.get("site.tilt") is not None:
        kwargs["tilt"] = float(eff["site.tilt"])
    if eff.get("site.azimuth") is not None:
        kwargs["azimuth"] = float(eff["site.azimuth"])
    return MockSolarForecastSource(tz, **kwargs)


@register_forecast_provider(
    "forecast_solar",
    metadata=AdapterMetadata(
        label="Forecast.Solar",
        description="Keyless public solar forecast (default baseline).",
        capabilities=frozenset({"p50_derived_bands", "fallback_on_failure"}),
    ),
)
def _build_forecast_solar(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    fallback: object | None = None,
    http_get: object | None = None,
    clock: Callable[[], datetime] | None = None,
    horizon_slots: int | None = None,
    **_: Any,
) -> Any:
    from ems.sources.forecast_solar import ForecastSolarSource

    return ForecastSolarSource(
        tz=tz,
        cache_store=cache_store,
        fallback=fallback,
        **_site_kw(eff),
        **_optional_transport_kw(http_get=http_get, clock=clock, horizon_slots=horizon_slots),
    )


@register_forecast_provider(
    "solcast",
    metadata=AdapterMetadata(
        label="Solcast",
        description=(
            "Hobbyist rooftop forecast with real P10/P50/P90; falls back to Forecast.Solar."
        ),
        capabilities=frozenset({"p10", "p50", "p90", "daily_budget", "fallback_on_failure"}),
        config_fields=(
            AdapterConfigField(
                key="solar.solcast_api_key", type="secret", secret=True, required=True,
                label="Solcast API key",
            ),
            AdapterConfigField(
                key="solar.solcast_resource_id", type="text", required=True,
                label="Solcast rooftop resource id",
            ),
        ),
    ),
)
def _build_solcast(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    fallback: object | None = None,
    http_get: object | None = None,
    clock: Callable[[], datetime] | None = None,
    horizon_slots: int | None = None,
    **_: Any,
) -> Any | None:
    """Build Solcast when credentials exist; return None so the factory can fall back.

    Contract tests may inject `http_get` without credentials — treat that as complete config so
    the hermetic suite can exercise Solcast itself (not only the incomplete-config fallback).
    """
    api_key = (eff.get("solar.solcast_api_key") or "").strip()
    resource_id = (eff.get("solar.solcast_resource_id") or "").strip()
    if (not api_key or not resource_id) and http_get is None:
        _log.warning(
            "solar.forecast_provider=solcast but api key / resource id missing; "
            "using Forecast.Solar"
        )
        return None
    from ems.sources.solcast import SolcastSource

    return SolcastSource(
        tz=tz,
        api_key=api_key or "contract-test",
        resource_id=resource_id or "contract-test",
        daily_budget=int(eff.get("solar.solcast_daily_call_budget") or 10),
        fallback=fallback,
        cache_store=cache_store,
        **_optional_transport_kw(http_get=http_get, clock=clock, horizon_slots=horizon_slots),
    )


def build_solar_forecast(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    use_live: bool | None = None,
    http_get: object | None = None,
    solcast_http_get: object | None = None,
) -> Any:
    """Construct the solar forecast source for the effective settings.

    - Mock curve when live devices / site lat-lon are not available.
    - Otherwise look up `solar.forecast_provider` in the registry (default `forecast_solar`).
    - Solcast always receives Forecast.Solar as its SPEC fallback; unknown providers fall back to
      Forecast.Solar as well (fail safe — never worse than the keyless path).

    ``http_get`` is the Forecast.Solar ``(url) -> dict`` transport; ``solcast_http_get`` is the
    Solcast ``(url, headers) -> dict`` transport (different arity — keep them separate).
    """
    live = bool(use_live) if use_live is not None else (
        bool(eff.get("connection.use_live_devices")) and bool(eff.get("meters.p1_ip"))
    )
    mock_builder = get_builder(FORECAST_DOMAIN, "mock")
    if mock_builder is None:  # pragma: no cover — registered at import
        raise RuntimeError("mock forecast adapter missing from registry")
    if not live or eff.get("site.lat") is None or eff.get("site.lon") is None:
        return mock_builder(eff, tz, cache_store=cache_store)

    provider = str(eff.get("solar.forecast_provider") or "forecast_solar").strip().lower()
    if provider == "mock":
        return mock_builder(eff, tz, cache_store=cache_store)

    # Forecast.Solar is always available as the keyless baseline / Solcast fallback.
    # Build via the registered builder so vendor import stays behind the registry.
    baseline_builder = get_builder(FORECAST_DOMAIN, "forecast_solar")
    if baseline_builder is None:  # pragma: no cover — registered at import
        raise RuntimeError("forecast_solar adapter missing from registry")
    baseline = baseline_builder(eff, tz, cache_store=cache_store, http_get=http_get)
    if provider == "forecast_solar":
        return baseline

    builder = get_builder(FORECAST_DOMAIN, provider)
    if builder is None:
        _log.warning("unknown solar.forecast_provider=%r; using Forecast.Solar", provider)
        return baseline

    # Only the chosen provider's builder runs here (lazy). Baseline was built because Solcast
    # needs it as SPEC fallback / incomplete-config path — not because every registered builder
    # is eagerly constructed. Solcast uses a headers-aware GET; fall back to http_get only if
    # the caller didn't supply a dedicated transport (tests often inject one shape).
    built = builder(
        eff, tz, cache_store=cache_store, fallback=baseline,
        http_get=solcast_http_get if solcast_http_get is not None else http_get,
    )
    return built if built is not None else baseline
