from datetime import UTC, datetime

import pytest

from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.indevolt import BatteryUnavailable
from ems.sources.indevolt_driver import (
    IndevoltBatteryDriver,
    mode_from_data,
    setdata_writes,
)

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


class FakeIndevolt:
    """In-memory stand-in (never the live battery). read_keys reflects state; post applies writes
    so the post-write confirm re-read works."""

    def __init__(self, mode=1, state=1000):
        self.state = {"7101": mode, "6001": state, "142": 5.38, "7120": 1000}
        self.writes: list[tuple[int, list[int]]] = []

    def read_keys(self, keys):
        return {str(k): self.state[str(k)] for k in keys if str(k) in self.state}

    def post(self, point, values):
        self.writes.append((point, values))
        if point == 47005:
            self.state["7101"] = values[0]  # mode
        elif point == 47015:
            self.state["6001"] = 1000 + values[0]  # 0/1/2 -> 1000/1001/1002
        return {"result": True}


def _driver(fake, armed=True):
    return IndevoltBatteryDriver("192.0.2.53", armed=armed, reader=fake, rpc_post=fake.post)


def test_setdata_write_mapping():
    assert setdata_writes(PhysicalMode.AUTO) == [(47005, [1])]
    assert setdata_writes(PhysicalMode.IDLE) == [(47005, [4]), (47015, [0])]
    assert setdata_writes(PhysicalMode.CHARGE, power_w=1500, target_soc=90) == [
        (47005, [4]), (47015, [1]), (47016, [1500]), (47017, [90])
    ]
    assert setdata_writes(PhysicalMode.DISCHARGE, target_soc=10)[1] == (47015, [2])
    # No default-to-full: a CHARGE/DISCHARGE write without an explicit target is a hard error.
    with pytest.raises(ValueError):
        setdata_writes(PhysicalMode.CHARGE)


def test_mode_from_data():
    assert mode_from_data({"7101": 1}) is PhysicalMode.AUTO
    assert mode_from_data({"7101": 4, "6001": 1000}) is PhysicalMode.IDLE
    assert mode_from_data({"7101": 4, "6001": 1001}) is PhysicalMode.CHARGE
    assert mode_from_data({"7101": 4, "6001": 1002}) is PhysicalMode.DISCHARGE
    assert mode_from_data({}) is PhysicalMode.AUTO  # safe default


def test_armed_is_read_only():
    drv = IndevoltBatteryDriver("x", reader=FakeIndevolt())
    assert drv.armed is False
    with pytest.raises(AttributeError):
        drv.armed = True


def test_unarmed_driver_never_writes():
    fake = FakeIndevolt()
    assert _driver(fake, armed=False).apply(PhysicalMode.CHARGE, target_soc=90) is False
    assert fake.writes == []


def test_charge_without_target_is_refused_no_write():
    # The driver must NEVER charge to a default — a target-less charge is refused before any write.
    fake = FakeIndevolt(mode=1, state=1000)
    assert _driver(fake).apply(PhysicalMode.CHARGE) is False
    assert fake.writes == []


def test_default_driver_has_no_write_transport():
    drv = IndevoltBatteryDriver("x", armed=True, reader=FakeIndevolt())  # rpc_post = refusing stub
    assert drv.apply(PhysicalMode.CHARGE, target_soc=90) is False


def test_armed_apply_issues_correct_writes_and_confirms():
    fake = FakeIndevolt(mode=1, state=1000)  # starts in self-consumption
    assert _driver(fake).apply(PhysicalMode.CHARGE, target_soc=85, power_w=1800) is True
    assert fake.writes == [(47005, [4]), (47015, [1]), (47016, [1800]), (47017, [85])]


def test_apply_false_when_confirm_mismatches():
    class StuckSelf(FakeIndevolt):
        def post(self, point, values):  # accept writes but never change the reported mode
            self.writes.append((point, values))
            return {"result": True}

    assert _driver(StuckSelf()).apply(PhysicalMode.CHARGE, target_soc=90) is False


def test_probe_reports_capabilities_else_unavailable():
    cap = _driver(FakeIndevolt()).probe()
    assert "charge" in cap.services and cap.p1_paired is True
    empty = IndevoltBatteryDriver("x", reader=type("E", (), {"read_keys": lambda s, k: {}})())
    with pytest.raises(BatteryUnavailable):
        empty.probe()


def test_full_control_chain_commands_driver_when_controlling():
    # Hands end-to-end against a MOCK device: CHARGE intent -> decide -> apply -> correct writes.
    fake = FakeIndevolt(mode=1, state=1000)
    driver = _driver(fake)
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    assert lc.tick(NOW).value == "controlling"
    decision = ModeController(driver, lc, dry_run=False).decide(
        BatteryIntent.GRID_CHARGE_TO_TARGET, NOW, target_soc=80, power_w=2000
    )
    assert decision.applied is True and decision.outcome == "applied"
    assert (47015, [1]) in fake.writes  # commanded charge state
    assert (47017, [80]) in fake.writes  # ...to the plan's target SoC, not a default 100
