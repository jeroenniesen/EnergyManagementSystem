"""Carbon adapter factory on the generic registry (#113 slice b)."""
from __future__ import annotations

import pytest

from ems.sources import carbon_factory as cf
from ems.sources.carbon import ElectricityMapsCarbonSource, StaticCarbonSource
from ems.sources.carbon_factory import (
    CARBON_DOMAIN,
    build_carbon_source,
    register_carbon_provider,
    registered_providers,
)
from ems.sources.ports import CarbonSource
from ems.sources.registry import get_metadata, registered_adapters


def test_registered_providers_match_registry_domain():
    assert set(registered_providers()) == set(registered_adapters(CARBON_DOMAIN))
    # Settings order: static (default) first, then remaining names sorted.
    assert registered_providers()[0] == "static"
    assert "electricitymaps" in registered_providers()


def test_duplicate_carbon_registration_fails():
    with pytest.raises(ValueError, match="already registered"):
        @register_carbon_provider("static")
        def _dup(eff, **_):  # pragma: no cover
            return None


def test_build_static_default():
    src = build_carbon_source({})
    assert isinstance(src, StaticCarbonSource)
    assert src.factor == 0.27


def test_build_static_uses_configured_factor():
    src = build_carbon_source({"reporting.grid_co2_factor": 0.15})
    assert isinstance(src, StaticCarbonSource)
    assert src.factor == 0.15


def test_electricitymaps_with_key():
    src = build_carbon_source({
        "reporting.carbon_signal": "electricitymaps",
        "reporting.electricitymaps_api_key": "test-key",
    })
    assert isinstance(src, ElectricityMapsCarbonSource)


def test_electricitymaps_without_key_falls_back_to_static():
    src = build_carbon_source({
        "reporting.carbon_signal": "electricitymaps",
        "reporting.grid_co2_factor": 0.22,
    })
    assert isinstance(src, StaticCarbonSource)
    assert src.factor == 0.22


def test_unknown_signal_falls_back_to_static():
    src = build_carbon_source({
        "reporting.carbon_signal": "not_a_real_provider",
        "reporting.grid_co2_factor": 0.31,
    })
    assert isinstance(src, StaticCarbonSource)
    assert src.factor == 0.31


def test_adapters_satisfy_carbon_port():
    static = build_carbon_source({})
    live = build_carbon_source({
        "reporting.carbon_signal": "electricitymaps",
        "reporting.electricitymaps_api_key": "k",
    })
    assert isinstance(static, CarbonSource)
    assert isinstance(live, CarbonSource)


def test_metadata_lists_config_fields():
    meta = get_metadata(CARBON_DOMAIN, "electricitymaps")
    assert meta is not None
    keys = {f.key for f in meta.config_fields}
    assert "reporting.electricitymaps_api_key" in keys


def test_connection_build_carbon_source_delegates():
    from ems.connection import build_carbon_source as conn_build

    src = conn_build({"reporting.carbon_signal": "static", "reporting.grid_co2_factor": 0.19})
    assert isinstance(src, StaticCarbonSource)
    assert src.factor == 0.19


# Keep import of module under test for coverage of registration side effects.
assert cf.CARBON_DOMAIN == "carbon"
