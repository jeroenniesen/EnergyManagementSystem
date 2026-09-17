"""Hermetic conformance checks for the external source adapters.

These tests deliberately inject every transport.  They exercise the public port methods and the
fail-safe behavior used when an upstream is unavailable; no network request or battery write is
allowed here.
"""

from datetime import UTC, datetime
from inspect import Parameter, signature
from typing import get_type_hints
from zoneinfo import ZoneInfo

import pytest

from ems.domain import PhysicalMode
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import ForecastSlot, MockSolarForecastSource
from ems.sources.forecast_solar import ForecastSolarSource
from ems.sources.indevolt import BatteryUnavailable, IndevoltReadClient
from ems.sources.indevolt_driver import IndevoltBatteryDriver
from ems.sources.live import HomeWizardMeter, LiveSource, ev_w, grid_w, solar_w
from ems.sources.mock import MockSource
from ems.sources.ports import BatteryDriver, PriceSource, SolarForecastSource, Source
from ems.sources.prices import MockPriceSource, PriceSlot
from ems.sources.tibber import TibberPriceSource


def _method_shape(method: object) -> tuple[tuple[str, object, object], ...]:
    """Return the callable shape without the implementation's ``self`` parameter."""
    params = list(signature(method).parameters.values())
    if params and params[0].name in {"self", "cls"}:
        params = params[1:]
    return tuple((p.name, p.kind, p.default) for p in params)


def _assert_port_method(
    adapter: type, port: type, name: str, expected: tuple[tuple[str, object], ...] = (),
) -> None:
    implementation = getattr(adapter, name)
    contract = getattr(port, name)
    impl_sig = signature(implementation)
    port_sig = signature(contract)
    actual = _method_shape(implementation)
    assert tuple((name, kind) for name, kind, _default in actual) == expected, (
        f"{adapter.__name__}.{name} does not match {port.__name__}.{name}: "
        f"{impl_sig} != {port_sig}"
    )
    assert actual == _method_shape(contract), (
        f"{adapter.__name__}.{name} changes required arguments or defaults"
    )
    # Resolve the ports' forward references without importing adapters into production ports.
    port_hints = get_type_hints(
        contract, localns={"PriceSlot": PriceSlot, "ForecastSlot": ForecastSlot}
    )
    implementation_hints = get_type_hints(implementation)
    assert implementation_hints == port_hints, (
        f"{adapter.__name__}.{name} annotations differ from the port: "
        f"{implementation_hints} != {port_hints}"
    )



def test_all_current_adapters_match_port_signatures() -> None:
    """Keep structural ports honest even when an adapter does not inherit the protocol."""
    source_adapters = (MockSource, LiveSource)
    for adapter in source_adapters:
        _assert_port_method(adapter, Source, "read")

    battery_adapters = (IndevoltBatteryDriver, MockBatteryDriver)
    for adapter in battery_adapters:
        for method in ("probe", "current_mode", "apply"):
            expected = (
                (
                    ("mode", Parameter.POSITIONAL_OR_KEYWORD),
                    ("target_soc", Parameter.KEYWORD_ONLY),
                    ("power_w", Parameter.KEYWORD_ONLY),
                )
                if method == "apply"
                else ()
            )
            _assert_port_method(adapter, BatteryDriver, method, expected)

    for adapter in (TibberPriceSource, MockPriceSource):
        _assert_port_method(adapter, PriceSource, "slots")

    for adapter in (MockSolarForecastSource, ForecastSolarSource):
        _assert_port_method(adapter, SolarForecastSource, "slots")


def test_mock_source_conforms_and_returns_normalized_sample() -> None:
    source = MockSource(total_gas_m3=12.5)
    assert isinstance(source, Source)
    sample = source.read()
    assert sample.grid_power_w == 200.0
    assert sample.soc_pct == 55.0
    assert sample.total_gas_m3 == 12.5


def test_tibber_conforms_and_transport_failure_is_empty() -> None:
    payload = {
        "viewer": {"homes": [{"currentSubscription": {"priceInfo": {"today": [
            {"total": 0.25, "startsAt": "2026-07-29T12:00:00+02:00"},
        ], "tomorrow": []}}}]}
    }
    source = TibberPriceSource("token", http_post=lambda *_: payload)
    assert isinstance(source, PriceSource)
    assert len(source.slots()) == 4

    def unavailable(*_):
        raise OSError("upstream unavailable")

    assert TibberPriceSource("token", http_post=unavailable).slots() == []


def test_forecast_adapters_conform_and_forecast_solar_falls_back() -> None:
    tz = ZoneInfo("Europe/Amsterdam")
    def clock() -> datetime:
        return datetime(2026, 7, 29, 10, tzinfo=UTC)
    mock = MockSolarForecastSource(tz, kwp=2.0, clock=clock, horizon_slots=8)
    assert isinstance(mock, SolarForecastSource)
    assert len(mock.slots()) == 8

    fallback = MockSolarForecastSource(tz, kwp=2.0, clock=clock, horizon_slots=8)
    live = ForecastSolarSource(
        tz=tz, lat=52.0, lon=5.0, tilt=35, azimuth=0, kwp=2.0,
        horizon_slots=8, http_get=lambda _url: (_ for _ in ()).throw(OSError("offline")),
        fallback=fallback, clock=clock,
    )
    assert isinstance(live, SolarForecastSource)
    assert live.slots() == fallback.slots()
    assert live.source_label == "model (fallback)"


def test_homewizard_adapter_normalizes_signs_without_network() -> None:
    meter = HomeWizardMeter("192.0.2.1", http_get=lambda _url: {"active_power_w": -123.4})
    assert meter.read()["active_power_w"] == -123.4
    assert grid_w({"active_power_w": 42}) == 42.0
    assert solar_w({"active_power_w": -500}) == 500.0
    assert solar_w({"active_power_w": 25}) == 0.0
    assert ev_w({"active_power_w": -25}) == 0.0


def test_indevolt_read_conforms_and_failure_is_explicit() -> None:
    client = IndevoltReadClient(
        "192.0.2.2",
        rpc_post=lambda _keys: {"6002": 66, "6000": 800, "6001": 1002},
    )
    assert client.read_power_soc() == (800.0, 66.0)

    broken = IndevoltReadClient("192.0.2.2", rpc_post=lambda _keys: {})
    with pytest.raises(BatteryUnavailable):
        broken.read_power_soc()


def test_indevolt_driver_conforms_but_unarmed_apply_never_writes() -> None:
    calls: list[tuple[int, list[int]]] = []
    driver = IndevoltBatteryDriver(
        "192.0.2.2", armed=False, rpc_post=lambda point, values: calls.append((point, values)),
        reader=IndevoltReadClient(
            "192.0.2.2", rpc_post=lambda _keys: {
                "6002": 66, "6000": 0, "6001": 1000, "7101": 4,
                "142": 10, "7120": 1000,
            },
        ),
    )
    assert isinstance(driver, BatteryDriver)
    assert driver.apply(PhysicalMode.AUTO) is False
    assert calls == []


@pytest.mark.parametrize('broken', ['required_argument', 'argument_type', 'return_type'])
def test_port_check_rejects_incompatible_battery_adapters(broken):
    class RequiredArgument:
        def apply(self, mode: PhysicalMode, *, target_soc: float | None,
                  power_w: float | None = None) -> bool:
            return True

    class ArgumentType:
        def apply(self, mode: str, *, target_soc: float | None = None,
                  power_w: float | None = None) -> bool:
            return True

    class ReturnType:
        def apply(self, mode: PhysicalMode, *, target_soc: float | None = None,
                  power_w: float | None = None) -> str:
            return 'unconfirmed'

    adapter = {'required_argument': RequiredArgument, 'argument_type': ArgumentType,
               'return_type': ReturnType}[broken]
    expected = (("mode", Parameter.POSITIONAL_OR_KEYWORD),
                ("target_soc", Parameter.KEYWORD_ONLY), ("power_w", Parameter.KEYWORD_ONLY))
    with pytest.raises(AssertionError):
        _assert_port_method(adapter, BatteryDriver, 'apply', expected)
