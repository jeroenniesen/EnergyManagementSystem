from datetime import UTC, datetime

import pytest

from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.indevolt import BatteryUnavailable
from ems.sources.indevolt_driver import (
    IndevoltBatteryDriver,
    mode_from_registers,
    setdata_registers,
)

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


class FakeIndevolt:
    """A stateful in-memory stand-in for the device — used so write logic is exercised against a
    mock, never the live battery. read_raw() reflects state; post() captures + applies registers."""

    def __init__(self, mode_reg=1):
        self.state = {"47005": mode_reg}
        self.captured: dict | None = None
        self.posts = 0

    def read_raw(self):
        return dict(self.state)

    def post(self, _url, registers):
        self.captured = dict(registers)
        self.posts += 1
        self.state.update(registers)
        return {"ok": True}


def _driver(fake, armed=True):
    return IndevoltBatteryDriver("192.168.50.53", armed=armed, reader=fake, rpc_post=fake.post)


def test_setdata_register_mapping():
    assert setdata_registers(PhysicalMode.AUTO) == {"47005": 1}
    assert setdata_registers(PhysicalMode.IDLE) == {"47005": 4, "47015": 0}
    c = setdata_registers(PhysicalMode.CHARGE, power_w=1500, target_soc=90)
    assert c == {"47005": 4, "47015": 1, "47016": 1500, "47017": 90}
    d = setdata_registers(PhysicalMode.DISCHARGE, power_w=1000, target_soc=20)
    assert d == {"47005": 4, "47015": 2, "47016": 1000, "47017": 20}


def test_mode_from_registers_roundtrip():
    assert mode_from_registers({"47005": 1}) is PhysicalMode.AUTO
    assert mode_from_registers({"47005": 4, "47015": 0}) is PhysicalMode.IDLE
    assert mode_from_registers({"47005": 4, "47015": 1}) is PhysicalMode.CHARGE
    assert mode_from_registers({"47005": 4, "47015": 2}) is PhysicalMode.DISCHARGE
    assert mode_from_registers({}) is PhysicalMode.AUTO  # safe default


def test_armed_is_read_only():
    drv = IndevoltBatteryDriver("x", reader=FakeIndevolt())
    assert drv.armed is False
    with pytest.raises(AttributeError):
        drv.armed = True  # no setter -> cannot be flipped post-construction


def test_unarmed_driver_never_writes():
    fake = FakeIndevolt()
    assert _driver(fake, armed=False).apply(PhysicalMode.CHARGE) is False
    assert fake.posts == 0  # nothing was sent to the device


def test_default_driver_has_no_write_transport():
    # Even if armed, with no transport injected the default refuses -> no write, returns False.
    drv = IndevoltBatteryDriver("192.168.50.53", armed=True,
                                reader=FakeIndevolt())  # rpc_post defaults to the refusing stub
    assert drv.apply(PhysicalMode.CHARGE) is False


def test_armed_apply_issues_correct_setdata_and_confirms():
    fake = FakeIndevolt(mode_reg=1)  # starts in AUTO
    ok = _driver(fake).apply(PhysicalMode.CHARGE)
    assert ok is True
    assert fake.captured == {"47005": 4, "47015": 1, "47016": 2000, "47017": 100}


def test_apply_returns_false_when_confirm_mismatches():
    class StuckInAuto(FakeIndevolt):
        def post(self, _url, registers):  # accepts the write but the mode never changes
            self.captured = dict(registers)
            self.posts += 1
            return {"ok": True}

    fake = StuckInAuto(mode_reg=1)
    assert _driver(fake).apply(PhysicalMode.CHARGE) is False  # re-read still AUTO -> unconfirmed


def test_probe_raises_when_empty_else_reports():
    empty = IndevoltBatteryDriver("x", reader=type("E", (), {"read_raw": lambda s: {}})())
    with pytest.raises(BatteryUnavailable):
        empty.probe()
    cap = _driver(FakeIndevolt(mode_reg=4)).probe()
    assert "charge" in cap.services and cap.max_charge_w == 2000.0


def test_full_control_chain_commands_the_driver_when_controlling():
    # The hands working end-to-end (against a MOCK device): a CHARGE intent flows
    # plan-intent -> ModeController.decide -> driver.apply -> correct SetData, confirmed.
    fake = FakeIndevolt(mode_reg=1)
    driver = _driver(fake)  # armed, mock transport
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    assert lc.tick(NOW).value == "controlling"
    ctrl = ModeController(driver, lc, dry_run=False)
    decision = ctrl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, NOW)
    assert decision.applied is True
    assert decision.outcome == "applied"
    assert fake.captured == {"47005": 4, "47015": 1, "47016": 2000, "47017": 100}
