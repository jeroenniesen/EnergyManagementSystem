"""Generic adapter registry (epic #111 / #140).

Hermetic — no network, no hardware. Uses a private test domain so production forecast
registrations are left alone.
"""
from __future__ import annotations

import pytest

from ems.sources import registry as reg
from ems.sources.registry import (
    AdapterConfigField,
    AdapterMetadata,
    build_adapter,
    get_builder,
    get_metadata,
    register_adapter,
    registered_adapters,
)

_DOMAIN = "_test_adapter_registry"


@pytest.fixture(autouse=True)
def _isolate_domain():
    reg._clear_domain_for_tests(_DOMAIN)
    yield
    reg._clear_domain_for_tests(_DOMAIN)


def test_register_and_list():
    register_adapter(
        _DOMAIN, "alpha", lambda: "A",
        AdapterMetadata(label="Alpha", description="first"),
    )
    register_adapter(_DOMAIN, "beta", lambda: "B", AdapterMetadata(label="Beta"))
    assert registered_adapters(_DOMAIN) == ("alpha", "beta")
    assert get_metadata(_DOMAIN, "alpha").label == "Alpha"
    assert get_metadata(_DOMAIN, "alpha").description == "first"


def test_duplicate_registration_fails():
    register_adapter(_DOMAIN, "once", lambda: 1, AdapterMetadata(label="Once"))
    with pytest.raises(ValueError, match="already registered"):
        register_adapter(_DOMAIN, "once", lambda: 2, AdapterMetadata(label="Once"))
    # Case / whitespace normalize to the same key.
    with pytest.raises(ValueError, match="already registered"):
        register_adapter(_DOMAIN, " Once ", lambda: 3, AdapterMetadata(label="Once"))


def test_empty_domain_or_name_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        register_adapter("", "x", lambda: None, AdapterMetadata(label="x"))
    with pytest.raises(ValueError, match="non-empty"):
        register_adapter(_DOMAIN, "  ", lambda: None, AdapterMetadata(label="x"))


def test_lazy_build_skips_unchosen_builders():
    calls: list[str] = []

    def make(tag: str):
        def builder():
            calls.append(tag)
            return tag

        return builder

    register_adapter(_DOMAIN, "chosen", make("chosen"), AdapterMetadata(label="Chosen"))
    register_adapter(_DOMAIN, "other", make("other"), AdapterMetadata(label="Other"))
    # Registration must not construct adapters (no vendor import / network client yet).
    assert calls == []
    assert get_builder(_DOMAIN, "chosen") is not None
    assert calls == []

    out = build_adapter(_DOMAIN, "chosen")
    assert out == "chosen"
    assert calls == ["chosen"]


def test_unknown_name_falls_back_to_baseline():
    register_adapter(_DOMAIN, "baseline", lambda: "safe", AdapterMetadata(label="Baseline"))
    out = build_adapter(_DOMAIN, "not_registered", fallback="baseline")
    assert out == "safe"


def test_incomplete_config_none_falls_back():
    calls: list[str] = []

    def incomplete():
        calls.append("incomplete")
        return None

    def baseline():
        calls.append("baseline")
        return "safe"

    register_adapter(_DOMAIN, "needs_creds", incomplete, AdapterMetadata(label="Needs"))
    register_adapter(_DOMAIN, "baseline", baseline, AdapterMetadata(label="Baseline"))
    out = build_adapter(_DOMAIN, "needs_creds", fallback="baseline")
    assert out == "safe"
    assert calls == ["incomplete", "baseline"]


def test_sync_and_async_ports_share_same_build_api():
    """Registry wiring is identical for sync ports and async-port objects (no await in registry)."""

    class SyncPrice:
        def slots(self) -> list:
            return [1]

    class AsyncCarbon:
        async def current_intensity(self) -> float | None:
            return 0.25

    class AsyncHa:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def get_state(self, entity_id: str) -> str:
            return "ok"

    register_adapter(
        _DOMAIN, "price", lambda: SyncPrice(),
        AdapterMetadata(label="Price", capabilities=frozenset({"slots"})),
    )
    register_adapter(
        _DOMAIN, "carbon", lambda: AsyncCarbon(),
        AdapterMetadata(label="Carbon", capabilities=frozenset({"async_intensity"})),
    )
    register_adapter(
        _DOMAIN, "ha", lambda: AsyncHa(),
        AdapterMetadata(label="HA", capabilities=frozenset({"async_context"})),
    )

    price = build_adapter(_DOMAIN, "price")
    carbon = build_adapter(_DOMAIN, "carbon")
    ha = build_adapter(_DOMAIN, "ha")
    assert price.slots() == [1]
    assert hasattr(carbon, "current_intensity")
    assert hasattr(ha, "__aenter__")


def test_metadata_config_fields_and_mapping_form():
    register_adapter(
        _DOMAIN,
        "mapped",
        lambda: "m",
        {
            "label": "Mapped",
            "description": "via dict",
            "capabilities": ["a", "b"],
            "config_fields": [
                {"key": "x.token", "type": "secret", "secret": True, "required": True},
            ],
        },
    )
    meta = get_metadata(_DOMAIN, "mapped")
    assert meta is not None
    assert meta.label == "Mapped"
    assert meta.capabilities == frozenset({"a", "b"})
    assert meta.config_fields == (
        AdapterConfigField(key="x.token", type="secret", secret=True, required=True, label=""),
    )


def test_unknown_without_fallback_raises():
    with pytest.raises(LookupError, match="no fallback"):
        build_adapter(_DOMAIN, "missing")
