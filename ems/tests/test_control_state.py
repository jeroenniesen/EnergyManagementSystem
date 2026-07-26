"""Runtime control state persists across restarts (SPEC §13.3): a reboot must not reset the daily
switch cap, min-dwell timer, or the original vendor mode."""
import sqlite3
from datetime import UTC, datetime, timedelta

from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import MockBatteryDriver
from ems.storage.control_state import CONTROL_STATE_CORRUPT, ControlStateStore

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _controlling_lifecycle() -> Lifecycle:
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(NOW)
    return lc


def test_store_load_save_roundtrip(tmp_path):
    s = ControlStateStore(str(tmp_path / "ems.sqlite"))
    s.init()
    assert s.load() == {}  # nothing yet
    s.save({"switches_today": 3, "last_confirmed_action": "charge"})
    assert s.load() == {"switches_today": 3, "last_confirmed_action": "charge"}
    # second instance (mimics a restart) reads the same row
    assert ControlStateStore(str(tmp_path / "ems.sqlite")).load()["switches_today"] == 3


def test_store_tolerates_missing_table_and_garbage(tmp_path):
    assert ControlStateStore(str(tmp_path / "absent.sqlite")).load() == {}  # no table → {}


def test_decide_persists_state_via_callback(tmp_path):
    s = ControlStateStore(str(tmp_path / "ems.sqlite"))
    s.init()
    ctl = ModeController(MockBatteryDriver(), _controlling_lifecycle(), dry_run=False,
                         on_state_change=s.save)
    ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, NOW, target_soc=80)
    saved = s.load()
    assert saved["switches_today"] == 1
    assert saved["last_requested_action"] == "charge" and saved["last_confirmed_action"] == "charge"
    assert saved["original_vendor_mode"] == "auto"  # captured the pre-control vendor mode


def test_restore_state_survives_restart_keeping_switch_count(tmp_path):
    s = ControlStateStore(str(tmp_path / "ems.sqlite"))
    s.init()
    ctl = ModeController(MockBatteryDriver(), _controlling_lifecycle(), dry_run=False,
                         on_state_change=s.save, max_switches_per_day=1)
    ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, NOW, target_soc=80)  # uses the 1 allowed switch

    # "Restart": a brand-new controller on the same store. Without restore it would have a fresh
    # 0/cap; with restore the cap is already spent, so a different intent is held.
    ctl2 = ModeController(MockBatteryDriver(), _controlling_lifecycle(), dry_run=False,
                          on_state_change=s.save, max_switches_per_day=1)
    ctl2.restore_state(s.load())
    assert ctl2.switches_today == 1
    blocked = ctl2.decide(BatteryIntent.HOLD_RESERVE, NOW + timedelta(hours=1))
    assert blocked.outcome == "cap_reached" and blocked.applied is False


def test_restore_state_ignores_garbage():
    ctl = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    ctl.restore_state({"switches_today": "not-an-int", "last_switch_at": "garbage"})
    assert ctl.switches_today == 0  # tolerant: bad blob → clean in-memory state


def test_load_signals_corrupt_row_distinct_from_absent(tmp_path):
    """The REAL store→restore boundary (the coverage the old direct-field test missed): load() must
    tell "a row EXISTS but its JSON is corrupt" apart from "genuinely nothing persisted". The former
    fails SAFE (last_command_unconfirmed=True → block restart); the latter stays fresh (False)."""
    db = str(tmp_path / "ems.sqlite")
    s = ControlStateStore(db)
    s.init()

    # Genuinely CORRUPT: a row EXISTS under the controller key but its value is not valid JSON.
    con = sqlite3.connect(db)
    con.execute("INSERT INTO control_state (key, value) VALUES (?, ?)", ("controller", "{not json"))
    con.commit()
    con.close()

    loaded = s.load()
    assert loaded is CONTROL_STATE_CORRUPT  # NOT collapsed to {} (the fail-open bug)
    ctl = ModeController(MockBatteryDriver(), _controlling_lifecycle(), dry_run=False)
    ctl.restore_state(loaded)
    assert ctl.last_command_unconfirmed is True  # fail SAFE on a corrupt persisted blob


def test_load_absent_row_is_fresh_not_corrupt(tmp_path):
    """A genuinely-ABSENT store (table exists, NO row) stays {} → fresh (last_command_unconfirmed
    False). Proves the corrupt-detection did NOT turn "nothing persisted yet" into a fail-safe
    block — a fresh boot must still be able to restart."""
    s = ControlStateStore(str(tmp_path / "ems.sqlite"))
    s.init()  # table exists, no row
    loaded = s.load()
    assert loaded == {}
    ctl = ModeController(MockBatteryDriver(), _controlling_lifecycle(), dry_run=False)
    ctl.restore_state(loaded)
    assert ctl.last_command_unconfirmed is False  # fresh, NOT fail-safe


def test_snapshot_roundtrips_through_restore():
    ctl = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    ctl.switches_today = 4
    ctl.last_switch_at = NOW
    ctl.original_vendor_mode = PhysicalMode.AUTO
    snap = ctl.state_snapshot()
    other = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    other.restore_state(snap)
    assert other.switches_today == 4 and other.last_switch_at == NOW
    assert other.original_vendor_mode is PhysicalMode.AUTO
