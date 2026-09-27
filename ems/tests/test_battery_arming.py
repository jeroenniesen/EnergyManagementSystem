"""Epic #111 I3 — parameterized arming / spy-transport tests (#139).

For every battery adapter (Indevolt + mock), inject a spy write transport and prove that
unarmed OR dry-run yields zero transport calls. Hermetic: no network, no live hardware.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import MockBatteryDriver
from ems.sources.indevolt import IndevoltReadClient
from ems.sources.indevolt_driver import IndevoltBatteryDriver
from ems.sources.ports import BatteryDriver

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

AdapterFactory = Callable[[list, bool], BatteryDriver]


def _indevolt_factory(calls: list, armed: bool) -> BatteryDriver:
    def spy(point: int, values: list[int]) -> dict:
        calls.append((point, list(values)))
        return {"result": True}

    reader = IndevoltReadClient(
        "192.0.2.2",
        rpc_post=lambda _keys: {
            "6002": 66, "6000": 0, "6001": 1000, "7101": 4,
            "142": 10, "7120": 1000,
        },
    )
    return IndevoltBatteryDriver(
        "192.0.2.2", armed=armed, rpc_post=spy, reader=reader,
    )


def _mock_factory(calls: list, armed: bool) -> BatteryDriver:
    def spy(mode: PhysicalMode, *, target_soc=None, power_w=None) -> None:
        calls.append((mode, target_soc, power_w))

    return MockBatteryDriver(armed=armed, write_transport=spy)


ADAPTER_FACTORIES: list[tuple[str, AdapterFactory]] = [
    ("IndevoltBatteryDriver", _indevolt_factory),
    ("MockBatteryDriver", _mock_factory),
]


def _controlling_lifecycle() -> Lifecycle:
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(T0 + timedelta(seconds=121))  # -> CONTROLLING
    return lc


@pytest.mark.parametrize("name,factory", ADAPTER_FACTORIES, ids=[n for n, _ in ADAPTER_FACTORIES])
def test_i3_unarmed_apply_zero_transport_calls(name: str, factory: AdapterFactory) -> None:
    """I3 / #139: unarmed adapter never touches the injected spy transport."""
    calls: list = []
    driver = factory(calls, False)
    assert isinstance(driver, BatteryDriver)
    assert driver.armed is False
    assert driver.apply(PhysicalMode.CHARGE, target_soc=80.0, power_w=1000.0) is False
    assert calls == [], f"{name}: unarmed apply must not call transport"


@pytest.mark.parametrize("name,factory", ADAPTER_FACTORIES, ids=[n for n, _ in ADAPTER_FACTORIES])
def test_i3_dry_run_decide_zero_transport_calls(name: str, factory: AdapterFactory) -> None:
    """I3 / #139: ModeController dry-run never reaches the transport (even when armed)."""
    calls: list = []
    driver = factory(calls, True)
    assert driver.armed is True
    ctl = ModeController(driver, Lifecycle(dry_run=True), dry_run=True)
    decision = ctl.decide(
        BatteryIntent.GRID_CHARGE_TO_TARGET, T0, target_soc=90.0, power_w=1500.0,
    )
    assert decision.outcome == "dry_run"
    assert decision.applied is False
    assert calls == [], f"{name}: dry-run decide must not call transport"


@pytest.mark.parametrize("name,factory", ADAPTER_FACTORIES, ids=[n for n, _ in ADAPTER_FACTORIES])
def test_i3_armed_live_decide_reaches_transport(name: str, factory: AdapterFactory) -> None:
    """Sanity: spy is wired — armed + not dry-run does call the transport once a write proceeds."""
    calls: list = []
    driver = factory(calls, True)
    ctl = ModeController(
        driver, _controlling_lifecycle(), dry_run=False, min_dwell_seconds=0.0,
    )
    now = T0 + timedelta(seconds=121)
    decision = ctl.decide(
        BatteryIntent.GRID_CHARGE_TO_TARGET, now, target_soc=85.0, power_w=1200.0,
    )
    assert decision.applied is True, f"{name}: expected applied, got {decision}"
    assert calls, f"{name}: armed live decide must call transport"


def test_api_calls_configure_power_limits_without_getattr() -> None:
    """#139: settings path uses the port member directly (no getattr duck-typing)."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "web" / "api.py").read_text(encoding="utf-8")
    assert 'getattr(controller.driver, "configure_power_limits"' not in source
    assert "getattr(controller.driver, 'configure_power_limits'" not in source
    assert "controller.driver.configure_power_limits(" in source


def test_application_protocols_reexports_sources_battery_driver() -> None:
    """#139: one BatteryDriver — application seam must not redefine a narrower copy."""
    from ems.application import protocols as app_protocols
    from ems.sources.ports import BatteryDriver as SourcesBatteryDriver

    assert app_protocols.BatteryDriver is SourcesBatteryDriver
