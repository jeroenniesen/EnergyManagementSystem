"""Grid CO₂ adapter factory — carbon domain on the generic registry (#113 slice b).

Adapters implement the shared `CarbonSource` port (`current_intensity() → float | None`).
This module is the **only** place that picks which adapter to construct from
`reporting.carbon_signal` — wiring (`ems/connection.py`) calls `build_carbon_source` and does
not branch on provider names itself.

Register a new provider with `@register_carbon_provider("name")` (delegates to
`ems.sources.registry.register_adapter` under `domain="carbon"`). Live ElectricityMaps requires
an API key; otherwise the credential-free static factor is used (fail safe).

Insights / reporting only — never feeds the planner or control path.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ems.sources.carbon import ElectricityMapsCarbonSource, StaticCarbonSource
from ems.sources.registry import (
    AdapterConfigField,
    AdapterMetadata,
    build_adapter,
    register_adapter,
    registered_adapters,
)

_log = logging.getLogger("ems.sources.carbon_factory")

CARBON_DOMAIN = "carbon"

# builder(eff, *, http=None, ...) -> CarbonSource | None
CarbonBuilder = Callable[..., Any]


def register_carbon_provider(
    name: str,
    *,
    metadata: AdapterMetadata | None = None,
) -> Callable[[CarbonBuilder], CarbonBuilder]:
    """Decorator: register `name` as a `reporting.carbon_signal` adapter id."""

    def deco(fn: CarbonBuilder) -> CarbonBuilder:
        meta = metadata or AdapterMetadata(label=name.strip().lower() or name)
        register_adapter(CARBON_DOMAIN, name, fn, meta)
        return fn

    return deco


def registered_providers() -> tuple[str, ...]:
    """Adapter names for Settings — `static` first (default), then the rest sorted."""
    names = list(registered_adapters(CARBON_DOMAIN))
    if "static" in names:
        names.remove("static")
        return ("static", *sorted(names))
    return tuple(sorted(names))


@register_carbon_provider(
    "static",
    metadata=AdapterMetadata(
        label="Flat grid factor",
        description="Credential-free flat kg CO₂/kWh from reporting.grid_co2_factor.",
        capabilities=frozenset({"reporting_only", "credential_free"}),
        config_fields=(
            AdapterConfigField(
                key="reporting.grid_co2_factor", type="number", required=False,
                label="Grid CO₂ factor",
            ),
        ),
    ),
)
def _build_static(eff: dict, *, http: object | None = None, **_: Any) -> StaticCarbonSource:
    del http
    factor = float(eff.get("reporting.grid_co2_factor") or 0.27)
    return StaticCarbonSource(factor)


@register_carbon_provider(
    "electricitymaps",
    metadata=AdapterMetadata(
        label="electricityMaps",
        description="Live grid intensity via electricityMaps free-tier API (reporting only).",
        capabilities=frozenset({"reporting_only", "network", "last_good_on_failure"}),
        config_fields=(
            AdapterConfigField(
                key="reporting.electricitymaps_api_key", type="secret", secret=True,
                required=True, label="electricityMaps API key",
            ),
            AdapterConfigField(
                key="reporting.grid_co2_factor", type="number", required=False,
                label="Fallback grid CO₂ factor",
            ),
        ),
    ),
)
def _build_electricitymaps(
    eff: dict, *, http: object | None = None, **_: Any,
) -> ElectricityMapsCarbonSource | None:
    api_key = (eff.get("reporting.electricitymaps_api_key") or "").strip()
    if not api_key:
        # Return None → build_adapter logs a single incomplete-config fallback warning.
        return None
    client = None
    if http is not None:
        from ems.http_client import make_json_get_headers

        client = make_json_get_headers(http, "best_effort")  # type: ignore[arg-type]
    return ElectricityMapsCarbonSource(api_key, client=client)


def build_carbon_source(eff: dict, *, http: object | None = None) -> Any:
    """Construct the CarbonSource for effective settings (fail-safe → static)."""
    name = (eff.get("reporting.carbon_signal") or "static").strip().lower() or "static"
    return build_adapter(
        CARBON_DOMAIN, name, eff, http=http, fallback="static",
    )
