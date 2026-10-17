"""Contract tests for ``Source`` implementations (``ems/sources/ports.py``).

Adapter-specific parsing, registry conformance, and port isinstance checks live in
``test_adapter_conformance.py``, ``test_live_source.py``, and ``test_source_ports.py`` —
this module only asserts the shared ``read()`` → ``RawSample`` contract for ``MockSource``
and ``LiveSource`` (injected HTTP/battery fakes, no LAN I/O).
"""
from __future__ import annotations

import math
from typing import Any

import pytest

from ems.domain import RawSample
from ems.sources.live import LiveSource
from ems.sources.mock import MockSource
from ems.sources.ports import Source
from ems.tests.test_live_source import CAR, P1, SOLAR, _FakeBattery, _meter


def _assert_sample_contract(source: Source, *, mock_load_identity: bool) -> None:
    assert isinstance(source, Source)
    sample = source.read()
    assert isinstance(sample, RawSample)
    for field in (
        "grid_power_w",
        "solar_power_w",
        "battery_power_w",
        "ev_power_w",
        "soc_pct",
    ):
        value = getattr(sample, field)
        assert isinstance(value, (int, float))
        assert math.isfinite(float(value))
    assert 0 <= sample.soc_pct <= 100
    if mock_load_identity:
        # SPEC §4 / docs/energy-model.md house-load identity in the mock steady state.
        assert sample.grid_power_w + sample.solar_power_w + sample.battery_power_w == 1000


@pytest.mark.parametrize(
    ("factory", "kwargs", "mock_load_identity", "extra"),
    [
        (MockSource, {}, True, {}),
        (MockSource, {"total_gas_m3": 12.5}, True, {"total_gas_m3": 12.5}),
        (
            LiveSource,
            {
                "p1": _meter(P1),
                "solar": _meter(SOLAR),
                "car": _meter(CAR),
                "battery": _FakeBattery(),
            },
            False,
            {},
        ),
    ],
    ids=["mock-default", "mock-gas", "live-fake-meters"],
)
def test_source_read_contract(factory, kwargs, mock_load_identity, extra):
    src = factory(**kwargs)
    _assert_sample_contract(src, mock_load_identity=mock_load_identity)
    for key, expected in extra.items():
        assert getattr(src.read(), key) == expected


def test_live_source_read_uses_injected_getter_only():
    calls: list[Any] = []

    def fake_get(url: str) -> dict:
        calls.append(url)
        return P1

    src = LiveSource(
        p1=_meter(P1),
        solar=_meter(SOLAR),
        car=_meter(CAR),
        battery=_FakeBattery(),
    )
    # Replace after construction so we can observe URLs from the composed meters.
    src.p1._get = fake_get  # type: ignore[attr-defined]
    src.read()
    assert calls and all("192.0.2.92" in u for u in calls)
