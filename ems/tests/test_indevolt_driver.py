from datetime import UTC, datetime

import pytest

from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import BatteryWriteUnconfirmed
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
    # Charge = SEPARATE single-value writes (the working HA form): real-time mode, power, soc, then
    # state LAST. NOT a combined 47015=[state,power,soc] (the docs' form, which the device ignores).
    assert setdata_writes(PhysicalMode.CHARGE, power_w=1500, target_soc=90) == [
        (47005, [4]), (47016, [1500]), (47017, [90]), (47015, [1])
    ]
    assert setdata_writes(PhysicalMode.DISCHARGE, power_w=800, target_soc=10)[-1] == (47015, [2])
    # Out-of-range power/SoC are clamped to the device limits (50–2400 W, 5–100 %).
    assert setdata_writes(PhysicalMode.CHARGE, power_w=4000, target_soc=100)[1:3] == [
        (47016, [2400]), (47017, [100])]
    assert setdata_writes(PhysicalMode.CHARGE, power_w=10, target_soc=2)[1:3] == [
        (47016, [50]), (47017, [5])]
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


def test_armed_apply_issues_correct_writes_and_is_accepted():
    fake = FakeIndevolt(mode=1, state=1000)  # starts in self-consumption
    assert _driver(fake).apply(PhysicalMode.CHARGE, target_soc=85, power_w=1800) is True
    assert fake.writes == [(47005, [4]), (47016, [1800]), (47017, [85]), (47015, [1])]


def test_apply_true_on_acceptance_even_if_mode_not_yet_reflected():
    # The device applies the switch with latency. apply() must NOT fail/revert just because the
    # immediate state still reads self-consumption — it returns True once the writes are ACCEPTED
    # (result:true); the control loop verifies the real mode on its next read.
    class StuckSelf(FakeIndevolt):
        def post(self, point, values):  # accept writes but report no immediate mode change
            self.writes.append((point, values))
            return {"result": True}

    assert _driver(StuckSelf()).apply(PhysicalMode.CHARGE, target_soc=90) is True


def test_apply_false_when_a_write_is_rejected():
    # A genuinely REJECTED write (result:false) must fail so the controller falls back to AUTO.
    class Rejects(FakeIndevolt):
        def post(self, point, values):
            self.writes.append((point, values))
            return {"result": False}

    assert _driver(Rejects()).apply(PhysicalMode.CHARGE, target_soc=90) is False


def test_apply_commands_every_tower_with_power_split():
    # Cluster of 2: the command must reach BOTH towers (a slave does NOT follow the master), and the
    # cluster power is split across them (each then clamped to device limits in setdata_writes).
    writes: dict[str, list] = {}

    def factory(ip):
        def post(point, values):
            writes.setdefault(ip, []).append((point, values))
            return {"result": True}
        return post

    drv = IndevoltBatteryDriver("10.0.0.1", armed=True, extra_ips=["10.0.0.2"],
                                post_factory=factory, reader=FakeIndevolt())
    assert drv.apply(PhysicalMode.CHARGE, target_soc=90, power_w=2000) is True
    assert set(writes) == {"10.0.0.1", "10.0.0.2"}  # both towers commanded
    # 2000 W cluster split → 1000 W/tower; each gets the full real-time sequence ending in state.
    for ip in ("10.0.0.1", "10.0.0.2"):
        assert (47016, [1000]) in writes[ip]
        assert writes[ip][0] == (47005, [4]) and writes[ip][-1] == (47015, [1])


def test_apply_false_if_any_tower_rejects():
    # If ANY tower rejects, apply() fails so the controller falls back to AUTO — never leaves a
    # half-commanded cluster (master charging, slave self-consuming).
    def factory(ip):
        def post(point, values):
            return {"result": ip == "10.0.0.1"}  # the slave (.2) rejects every write
        return post

    drv = IndevoltBatteryDriver("10.0.0.1", armed=True, extra_ips=["10.0.0.2"],
                                post_factory=factory, reader=FakeIndevolt())
    assert drv.apply(PhysicalMode.CHARGE, target_soc=90, power_w=2000) is False


def test_duplicate_tower_ip_is_commanded_once():
    # The master IP appearing again in extra_ips must not double-command it.
    drv = IndevoltBatteryDriver("10.0.0.1", armed=True, extra_ips=["10.0.0.1", " "],
                                reader=FakeIndevolt())
    assert drv.ips == ["10.0.0.1"]


def test_apply_raises_unconfirmed_on_transport_timeout():
    # A write that keeps timing out must RAISE BatteryWriteUnconfirmed (NOT return False) so the
    # controller HOLDS instead of reverting — the device is slow and likely got the command. This
    # was the live failure: a 4s timeout false-failed the charge and the AUTO-revert spiral lost it.
    def factory(ip):
        def post(point, values):
            raise TimeoutError("timed out")
        return post

    drv = IndevoltBatteryDriver("10.0.0.1", armed=True, post_factory=factory,
                                reader=FakeIndevolt(), write_retry_backoff=0)
    with pytest.raises(BatteryWriteUnconfirmed):
        drv.apply(PhysicalMode.CHARGE, target_soc=90)


def test_apply_retries_a_transient_timeout_then_succeeds():
    # A transient slow response is retried; if the retry lands, the write succeeds (True) — no
    # spurious failure just because the device was briefly busy.
    calls = {"n": 0}

    def factory(ip):
        def post(point, values):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("briefly slow")  # first write times out once
            return {"result": True}
        return post

    drv = IndevoltBatteryDriver("10.0.0.1", armed=True, post_factory=factory,
                                reader=FakeIndevolt(), write_attempts=3, write_retry_backoff=0)
    assert drv.apply(PhysicalMode.CHARGE, target_soc=90) is True


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
    # Separate writes: charge state, power, and the plan's target SoC (not a default 100).
    assert (47015, [1]) in fake.writes and (47017, [80]) in fake.writes and (47016, [2000]) in (
        fake.writes)
