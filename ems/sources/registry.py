"""Generic adapter registry for external source domains (epic #111 / #140).

One place to register vendor adapters by `(domain, name)`. Builders stay **lazy**: registration
only stores the callable; the vendor module / network client is imported and constructed when that
name is chosen. The same API covers sync ports (`SolarForecastSource`, `PriceSource`) and async
ports (`CarbonSource`, `HaClient`) — the registry never awaits; callers use the returned object
through its port as before.

Pattern mirrors `ems/planner/factory.py` (#70): unknown or incomplete names fail safe to a
baseline/mock. Duplicate `(domain, name)` registration raises.

First consumer: `ems/sources/forecast_factory.py` (`domain="forecast"`). CO₂ and other domains
follow in later #113 / epic slices — do not wire them here.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("ems.sources.registry")

# builder(*args, **kwargs) -> adapter | None
# Returning None means "incomplete config / cannot build" → caller/registry falls back.
AdapterBuilder = Callable[..., Any]


@dataclass(frozen=True)
class AdapterConfigField:
    """Declarative config key for Settings / setup (consumed by later epic slices)."""

    key: str
    type: str  # text | secret | number | int | bool | enum — mirrors SettingsField kinds
    secret: bool = False
    required: bool = False
    label: str = ""


@dataclass(frozen=True)
class AdapterMetadata:
    """Human-facing + capability metadata for a registered adapter."""

    label: str
    description: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    config_fields: tuple[AdapterConfigField, ...] = ()


@dataclass(frozen=True)
class AdapterEntry:
    builder: AdapterBuilder
    metadata: AdapterMetadata


# domain -> name -> entry. Module-level so `@register_*` decorators work at import time.
_REGISTRY: dict[str, dict[str, AdapterEntry]] = {}


def register_adapter(
    domain: str,
    name: str,
    builder: AdapterBuilder,
    metadata: AdapterMetadata | Mapping[str, Any] | None = None,
) -> AdapterBuilder:
    """Register `builder` for `(domain, name)`. Does not call the builder (lazy).

    `metadata` may be an `AdapterMetadata` or a plain mapping with the same keys. Duplicate names
    in the same domain raise `ValueError`. Returns `builder` unchanged so this works as a decorator
    helper: `register_adapter("forecast", "solcast", fn, meta)`.
    """
    dom = _norm_key(domain, what="domain")
    key = _norm_key(name, what="adapter name")
    meta = _coerce_metadata(metadata, default_label=key)
    bucket = _REGISTRY.setdefault(dom, {})
    if key in bucket:
        raise ValueError(f"adapter already registered: {dom}/{key}")
    bucket[key] = AdapterEntry(builder=builder, metadata=meta)
    return builder


def registered_adapters(domain: str) -> tuple[str, ...]:
    """Sorted adapter names registered for `domain` (empty tuple if none)."""
    bucket = _REGISTRY.get(_norm_key(domain, what="domain"), {})
    return tuple(sorted(bucket))


def get_adapter(domain: str, name: str) -> AdapterEntry | None:
    """Return the registry entry without building the adapter."""
    bucket = _REGISTRY.get(_norm_key(domain, what="domain"), {})
    return bucket.get(_norm_key(name, what="adapter name"))


def get_builder(domain: str, name: str) -> AdapterBuilder | None:
    entry = get_adapter(domain, name)
    return entry.builder if entry is not None else None


def get_metadata(domain: str, name: str) -> AdapterMetadata | None:
    entry = get_adapter(domain, name)
    return entry.metadata if entry is not None else None


def build_adapter(
    domain: str,
    name: str,
    *args: Any,
    fallback: str | None = None,
    **kwargs: Any,
) -> Any:
    """Build the adapter for `name`, failing safe to `fallback` when needed.

    - Unknown name → log + build `fallback` (required when the primary is missing).
    - Builder returns `None` (incomplete config) → same fallback path.
    - Only the chosen builder is invoked (lazy): the fallback builder is not called when the
      primary succeeds with a non-None result.
    """
    dom = _norm_key(domain, what="domain")
    key = _norm_key(name, what="adapter name")
    entry = get_adapter(dom, key)
    if entry is None:
        _log.warning("unknown adapter %s/%s; falling back to %r", dom, key, fallback)
        return _build_fallback(dom, fallback, *args, **kwargs)

    built = entry.builder(*args, **kwargs)
    if built is not None:
        return built

    _log.warning(
        "adapter %s/%s returned None (incomplete config); falling back to %r",
        dom, key, fallback,
    )
    return _build_fallback(dom, fallback, *args, **kwargs)


def _build_fallback(domain: str, fallback: str | None, *args: Any, **kwargs: Any) -> Any:
    if fallback is None:
        raise LookupError(f"no adapter for domain={domain!r} and no fallback configured")
    fb_key = _norm_key(fallback, what="fallback name")
    entry = get_adapter(domain, fb_key)
    if entry is None:
        raise LookupError(f"fallback adapter not registered: {domain}/{fb_key}")
    built = entry.builder(*args, **kwargs)
    if built is None:
        raise LookupError(f"fallback adapter {domain}/{fb_key} returned None")
    return built


def _norm_key(value: str, *, what: str) -> str:
    key = (value or "").strip().lower()
    if not key:
        raise ValueError(f"{what} must be non-empty")
    return key


def _coerce_metadata(
    metadata: AdapterMetadata | Mapping[str, Any] | None,
    *,
    default_label: str,
) -> AdapterMetadata:
    if metadata is None:
        return AdapterMetadata(label=default_label)
    if isinstance(metadata, AdapterMetadata):
        return metadata
    caps = metadata.get("capabilities", frozenset())
    if not isinstance(caps, frozenset):
        caps = frozenset(caps or ())
    raw_fields = metadata.get("config_fields") or ()
    fields: list[AdapterConfigField] = []
    for item in raw_fields:
        if isinstance(item, AdapterConfigField):
            fields.append(item)
        else:
            fields.append(AdapterConfigField(
                key=str(item["key"]),
                type=str(item.get("type") or "text"),
                secret=bool(item.get("secret", False)),
                required=bool(item.get("required", False)),
                label=str(item.get("label") or ""),
            ))
    return AdapterMetadata(
        label=str(metadata.get("label") or default_label),
        description=str(metadata.get("description") or ""),
        capabilities=caps,
        config_fields=tuple(fields),
    )


def _clear_domain_for_tests(domain: str) -> None:
    """Test-only: drop all adapters for `domain` so cases stay hermetic."""
    _REGISTRY.pop(_norm_key(domain, what="domain"), None)
