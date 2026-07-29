"""Structural conformance checks for the source ports.

These tests intentionally use the runtime-checkable protocols as a small guard against adapters
drifting away from the composition contracts.
"""
from zoneinfo import ZoneInfo

from ems.sources.base import Source as LegacySource
from ems.sources.battery import BatteryDriver as LegacyBatteryDriver
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import (
    MockSolarForecastSource,
)
from ems.sources.forecast import (
    SolarForecastSource as LegacySolarForecastSource,
)
from ems.sources.mock import MockSource
from ems.sources.ports import BatteryDriver, PriceSource, SolarForecastSource, Source
from ems.sources.prices import MockPriceSource
from ems.sources.prices import PriceSource as LegacyPriceSource


def test_legacy_protocol_imports_are_compatibility_aliases() -> None:
    assert LegacySource is Source
    assert LegacyBatteryDriver is BatteryDriver
    assert LegacyPriceSource is PriceSource
    assert LegacySolarForecastSource is SolarForecastSource


def test_current_mocks_conform_to_source_ports() -> None:
    tz = ZoneInfo("Europe/Amsterdam")
    assert isinstance(MockSource(), Source)
    assert isinstance(MockBatteryDriver(), BatteryDriver)
    assert isinstance(MockPriceSource(tz), PriceSource)
    assert isinstance(MockSolarForecastSource(tz), SolarForecastSource)
