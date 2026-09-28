"""Electricity price adapter factory — second user of the generic registry (#114 slice a).

Adapters implement the shared `PriceSource` port (`slots() → list[PriceSlot]`). This module is
the **only** place that picks which price adapter to construct from settings — wiring
(`ems/connection.py`) calls `build_price_source` and does not branch on provider names itself.

Register a new provider with `@register_price_provider("name")` (delegates to
`ems.sources.registry.register_adapter` under `domain="price"`). Live Tibber requires a token +
`connection.use_live_prices`; otherwise the credential-free mock is used (fail safe).

Class names (`TibberPriceSource`, `MockPriceSource`) are part of API/UI provenance checks — do not
rename them lightly.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ems.sources.prices import MockPriceSource
from ems.sources.registry import (
    AdapterConfigField,
    AdapterMetadata,
    get_builder,
    register_adapter,
    registered_adapters,
)

_log = logging.getLogger("ems.sources.price_factory")

PRICE_DOMAIN = "price"

# builder(eff, tz, *, cache_store, clock, http_post, ...) -> PriceSource | None
PriceBuilder = Callable[..., Any]


def register_price_provider(
    name: str,
    *,
    metadata: AdapterMetadata | None = None,
) -> Callable[[PriceBuilder], PriceBuilder]:
    """Decorator: register `name` as a price adapter id under `domain="price"`."""

    def deco(fn: PriceBuilder) -> PriceBuilder:
        meta = metadata or AdapterMetadata(label=name.strip().lower() or name)
        register_adapter(PRICE_DOMAIN, name, fn, meta)
        return fn

    return deco


def registered_providers() -> tuple[str, ...]:
    return registered_adapters(PRICE_DOMAIN)


@register_price_provider(
    "mock",
    metadata=AdapterMetadata(
        label="Built-in demo prices",
        description="Credential-free day/night curve for mock/dev (SPEC §11.6).",
        capabilities=frozenset({"tz_aware_slots", "synthetic"}),
    ),
)
def _build_mock(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    clock: Callable[[], datetime] | None = None,
    http_post: object | None = None,
    horizon_slots: int | None = None,
    **_: Any,
) -> MockPriceSource:
    del eff, cache_store, http_post  # unused — mock needs no credentials / transport
    kwargs: dict[str, Any] = {}
    if clock is not None:
        kwargs["clock"] = clock
    if horizon_slots is not None:
        kwargs["horizon_slots"] = horizon_slots
    return MockPriceSource(tz, **kwargs)


@register_price_provider(
    "tibber",
    metadata=AdapterMetadata(
        label="Tibber",
        description="Day-ahead prices via Tibber GraphQL; last-good cache on outage.",
        capabilities=frozenset({"tz_aware_slots", "last_good_on_failure", "network"}),
        config_fields=(
            AdapterConfigField(
                key="prices.tibber_token", type="secret", secret=True, required=True,
                label="Tibber API token",
            ),
        ),
    ),
)
def _build_tibber(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    clock: Callable[[], datetime] | None = None,
    http_post: object | None = None,
    cache_ttl: object | None = None,
    retry_ttl: object | None = None,
    **_: Any,
) -> Any | None:
    """Build Tibber when a token (or an injected transport) is present; else None → mock."""
    token = (eff.get("prices.tibber_token") or "").strip()
    # Hermetic contract tests inject http_post without requiring a real token.
    if not token and http_post is None:
        _log.warning("prices provider=tibber but token missing; using mock prices")
        return None
    from ems.sources.tibber import TibberPriceSource

    kwargs: dict[str, Any] = {"tz": tz, "cache_store": cache_store}
    if clock is not None:
        kwargs["clock"] = clock
    if http_post is not None:
        kwargs["http_post"] = http_post
    if cache_ttl is not None:
        kwargs["cache_ttl"] = cache_ttl
    if retry_ttl is not None:
        kwargs["retry_ttl"] = retry_ttl
    return TibberPriceSource(token or "contract-test", **kwargs)


def build_price_source(
    eff: dict,
    tz: ZoneInfo,
    *,
    cache_store: object | None = None,
    use_live: bool | None = None,
) -> Any:
    """Construct the price source for the effective settings.

    - Mock curve when live prices are off or the Tibber token is missing.
    - Otherwise Tibber via the registry (fail-safe to mock on incomplete config).
    """
    token = (eff.get("prices.tibber_token") or "").strip()
    live = bool(use_live) if use_live is not None else (
        bool(eff.get("connection.use_live_prices")) and bool(token)
    )
    mock_builder = get_builder(PRICE_DOMAIN, "mock")
    if mock_builder is None:  # pragma: no cover — registered at import
        raise RuntimeError("mock price adapter missing from registry")
    if not live:
        return mock_builder(eff, tz, cache_store=cache_store)

    builder = get_builder(PRICE_DOMAIN, "tibber")
    if builder is None:  # pragma: no cover
        _log.warning("tibber adapter missing from registry; using mock prices")
        return mock_builder(eff, tz, cache_store=cache_store)

    built = builder(eff, tz, cache_store=cache_store)
    return built if built is not None else mock_builder(eff, tz, cache_store=cache_store)
