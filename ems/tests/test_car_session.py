"""Wiring tests for the car-charging battery behaviours (feat/car-charge-modes).

The pure decision core (`ems.control.car_mode`) is tested in `test_car_mode.py`. Here we test the
WIRING: the `intent_to_mode` car-session exception, the `ModeController` car_session/force path, the
bounded-command gate (`_decide_car_command`), and the guard substitution end-to-end through the app.
Mocked battery, no hardware — control-loop idioms (drive `decide()`/the app, assert on the mock).
"""
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.car_mode import CarModeAction, decide_car_mode_action
from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode, RawSample
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.planner.schedule import SLOT
from ems.sense import SIGNALS
from ems.settings import defaults as settings_defaults
from ems.sources.battery import BatteryWriteUnconfirmed, MockBatteryDriver, intent_to_mode
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import PriceSlot
from ems.storage.audit import AuditStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import (
    _CAR_SESSION_MAX_COMMANDS,
    _decide_car_command,
    _decide_car_session_end,
    create_app,
)

AMS = ZoneInfo("Europe/Amsterdam")
T0 = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)
DFL = BatteryIntent.DISCHARGE_FOR_LOAD


# ==================================================================================================
# 1. intent_to_mode — the narrow, deliberate car_session exception (battery.py)
# ==================================================================================================

def test_car_session_maps_discharge_for_load_to_discharge_even_without_export():
    # The whole point: while the car charges, a discharge behaviour becomes a REAL forced DISCHARGE
    # even though export-discharge is OFF (the fail-safe default).
    assert intent_to_mode(DFL, allow_export_discharge=False,
                          car_session=True) is PhysicalMode.DISCHARGE
    # ...but the DEFAULT (no car session, no export) is unchanged: DISCHARGE_FOR_LOAD → AUTO.
    assert intent_to_mode(DFL, allow_export_discharge=False) is PhysicalMode.AUTO
    # ...and the pre-existing export path is untouched.
    assert intent_to_mode(DFL, allow_export_discharge=True) is PhysicalMode.DISCHARGE


def test_car_session_flag_does_not_disturb_other_intents():
    for intent, mode in (
        (BatteryIntent.ALLOW_SELF_CONSUMPTION, PhysicalMode.AUTO),
        (BatteryIntent.GRID_CHARGE_TO_TARGET, PhysicalMode.CHARGE),
        (BatteryIntent.HOLD_RESERVE, PhysicalMode.IDLE),
    ):
        assert intent_to_mode(intent, car_session=True) is mode


# ==================================================================================================
# 2. ModeController — car_session mapping + force re-command + the unconfirmed HOLD path
# ==================================================================================================

def _controlling_lifecycle():
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(T0 + timedelta(seconds=121))  # -> CONTROLLING
    return lc


def test_decide_car_session_writes_a_real_discharge_with_the_setpoint():
    d = MockBatteryDriver()  # starts AUTO, allow_export_discharge defaults OFF
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(DFL, T0 + timedelta(seconds=200), target_soc=10.0, power_w=800.0,
                     car_session=True, force=True, priority=True)
    assert dec.outcome == "applied"
    assert d.current_mode() is PhysicalMode.DISCHARGE
    assert d.last_power_w == 800.0 and d.last_target_soc == 10.0


def test_force_lets_a_setpoint_re_command_through_mode_only_idempotency():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    t = T0 + timedelta(seconds=200)
    ctl.decide(DFL, t, target_soc=10.0, power_w=800.0, car_session=True, force=True, priority=True)
    assert d.current_mode() is PhysicalMode.DISCHARGE
    # Already DISCHARGE; a NEW setpoint with force=True must WRITE, not be swallowed as idempotent.
    dec = ctl.decide(DFL, t + timedelta(seconds=700), target_soc=10.0, power_w=1500.0,
                     car_session=True, force=True, priority=True)
    assert dec.outcome == "applied"
    assert d.last_power_w == 1500.0


def test_without_force_a_car_session_is_still_idempotent_when_already_discharging():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    t = T0 + timedelta(seconds=200)
    ctl.decide(DFL, t, target_soc=10.0, power_w=800.0, car_session=True, force=True, priority=True)
    # A quiet cycle (no re-command) does NOT force → mode-only idempotency holds the setpoint.
    dec = ctl.decide(DFL, t + timedelta(seconds=700), target_soc=10.0, power_w=850.0,
                     car_session=True, force=False, priority=True)
    assert dec.outcome == "idempotent"


def test_car_command_transport_timeout_holds_and_does_not_revert():
    # A timeout during a car command rides the established BatteryWriteUnconfirmed path: HOLD the
    # DISCHARGE, never revert to AUTO (reverting would also time out — the live ALERT spiral).
    class TimingOut(MockBatteryDriver):
        def apply(self, mode, *, target_soc=None, power_w=None):
            raise BatteryWriteUnconfirmed("timed out")

    d = TimingOut()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(DFL, T0 + timedelta(seconds=200), target_soc=10.0, power_w=800.0,
                     car_session=True, force=True, priority=True)
    assert dec.outcome == "unconfirmed"
    assert dec.desired_mode is PhysicalMode.DISCHARGE   # NOT reverted to AUTO
    assert ctl.switches_today == 1                      # counted so retries are spaced


def test_dry_run_car_session_never_writes():
    # The writer's OWN dry-run gate covers the car session — nothing re-implemented downstream.
    d = MockBatteryDriver()
    ctl = ModeController(d, Lifecycle(dry_run=True), dry_run=True)
    dec = ctl.decide(DFL, T0, target_soc=10.0, power_w=800.0,
                     car_session=True, force=True, priority=True)
    assert dec.outcome == "dry_run"
    assert dec.applied is False
    assert d.current_mode() is PhysicalMode.AUTO  # untouched


# ==================================================================================================
# 3. _decide_car_command — the bounded (re-)command gate (session box + 10-min dwell + cap)
# ==================================================================================================

def _fresh_session() -> dict:
    return {"active": False, "setpoint_w": None, "commanded_at": None, "commands": 0}


def _active_session(setpoint: float, commanded_at: datetime, commands: int = 1) -> dict:
    return {"active": True, "setpoint_w": setpoint,
            "commanded_at": commanded_at.isoformat(), "commands": commands}


def _disc(power: float, recommand: bool) -> CarModeAction:
    return CarModeAction("discharge", power, "covering the house", recommand)


def test_first_cycle_starts_the_session_and_commands():
    cmd, nxt, event = _decide_car_command(_fresh_session(), _disc(800.0, True), T0)
    assert cmd is True and event == "start"
    assert nxt == {"active": True, "setpoint_w": 800.0,
                   "commanded_at": T0.isoformat(), "commands": 1}


def test_small_stable_prediction_holds_the_setpoint_no_write():
    # car_mode already reports recommand=False for a small delta -> we hold, no command.
    session = _active_session(800.0, T0 - timedelta(minutes=30))
    cmd, nxt, event = _decide_car_command(session, _disc(850.0, False), T0)
    assert cmd is False and event == "hold"
    assert nxt["setpoint_w"] == 800.0 and nxt["commands"] == 1  # unchanged


def test_ten_minute_dwell_blocks_an_early_recommand_then_allows_it():
    session = _active_session(800.0, T0)  # just commanded at T0
    # A big jump (recommand=True) 5 min later is BLOCKED by the 10-min car dwell.
    early = _decide_car_command(session, _disc(1500.0, True), T0 + timedelta(minutes=5))
    assert early[0] is False and early[2] == "hold"
    # ...but 11 min later (dwell elapsed) it re-commands.
    late = _decide_car_command(session, _disc(1500.0, True), T0 + timedelta(minutes=11))
    assert late[0] is True and late[2] == "recommand"
    assert late[1]["setpoint_w"] == 1500.0 and late[1]["commands"] == 2


def test_command_cap_holds_and_flags_instead_of_re_commanding():
    # Budget spent: even a wanted re-command (past the dwell) is refused — hold the last setpoint.
    session = _active_session(1000.0, T0 - timedelta(hours=1), commands=_CAR_SESSION_MAX_COMMANDS)
    cmd, nxt, event = _decide_car_command(session, _disc(2000.0, True), T0)
    assert cmd is False and event == "cap"
    assert nxt["setpoint_w"] == 1000.0  # unchanged (we did NOT chase the moving prediction)


class _CountingDriver(MockBatteryDriver):
    def __init__(self) -> None:
        super().__init__()
        self.apply_calls = 0

    def apply(self, mode, *, target_soc=None, power_w=None):
        self.apply_calls += 1
        return super().apply(mode, target_soc=target_soc, power_w=power_w)


def test_write_count_proof_a_3h_noisy_session_stays_under_three_commands():
    # A full 3h match-home-load session (36 cycles @ 5 min) with a NOISY prediction: the battery is
    # commanded at most a handful of times, NOT once per cycle. This is the anti-tracking contract —
    # asserted on the MOCK (real controller.decide path), not just the pure gate.
    d = _CountingDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    session = _fresh_session()
    # 18 cycles of noise around 800 W (never > 500 W from the committed setpoint), then a sustained
    # step up to ~1600 W (one genuine re-command), then noise around 1600 W.
    preds = [800, 850, 750, 900, 800, 700, 850, 800, 950, 800,
             750, 800, 900, 850, 800, 750, 800, 850,
             1600, 1550, 1650, 1600, 1500, 1600, 1650, 1600, 1550, 1600,
             1600, 1650, 1550, 1600, 1600, 1550, 1650, 1600]
    now = T0 + timedelta(seconds=200)
    for p in preds:
        act = decide_car_mode_action(
            "match_home_load", car_charging=True, soc_pct=55.0, min_reserve_soc=10.0,
            max_discharge_w=4000.0, static_w=0.0, predicted_house_w=float(p),
            current_setpoint_w=session["setpoint_w"])
        cmd, nxt, _event = _decide_car_command(session, act, now)
        if cmd:
            ctl.decide(DFL, now, target_soc=10.0, power_w=act.power_w,
                       car_session=True, force=True, priority=True)
        session.update(nxt)  # mirror the closure: advance the box every cycle
        now += timedelta(minutes=5)
    assert d.apply_calls <= 3
    assert d.apply_calls >= 2  # the sustained step DID trigger a real re-command (not trivially 1)
    assert d.current_mode() is PhysicalMode.DISCHARGE


# ==================================================================================================
# 3b. _decide_car_session_end — the session-END hysteresis (below-threshold grace)
#
# Production audit: "car session started 13:22 -> ended 13:25 -> started 13:26" — the Tesla's
# charging power evidently dipped below control.car_charging_threshold_w for a single 5-min cycle
# (three-phase balancing / a charging ramp pause), so the session ended and immediately restarted,
# each flip issuing a battery mode command. `_decide_car_session_end` requires N (>=1) CONSECUTIVE
# below-threshold cycles before actually ending; `_control_tick` resets the counter to 0 directly
# on any cycle that reads ABOVE threshold (car_action.action == "discharge") — mirrored below via
# manual box mutation, exactly like the noisy-session write-count proof above.
# ==================================================================================================

def test_below_threshold_grace_holds_for_two_cycles_then_ends_on_the_third():
    session = {"below_threshold_cycles": 0}
    # Two below-threshold cycles: NOT ended, counter climbs 1 -> 2 (no end command either cycle).
    ended, cycles = _decide_car_session_end(session, car_below_threshold=True, end_cycles=3)
    assert (ended, cycles) == (False, 1)
    session["below_threshold_cycles"] = cycles
    ended, cycles = _decide_car_session_end(session, car_below_threshold=True, end_cycles=3)
    assert (ended, cycles) == (False, 2)
    session["below_threshold_cycles"] = cycles
    # The THIRD consecutive below-threshold cycle ends it — exactly once.
    ended, cycles = _decide_car_session_end(session, car_below_threshold=True, end_cycles=3)
    assert (ended, cycles) == (True, 0)


def test_counter_resets_when_a_cycle_reads_above_threshold():
    # 2 below (climbs to 2) -> 1 above (the wiring resets the counter directly, mirrored here) ->
    # 2 more below MUST NOT end (if the counter hadn't reset, 2+2=4 >= 3 would end early).
    session = {"below_threshold_cycles": 0}
    for _ in range(2):
        _, cycles = _decide_car_session_end(session, car_below_threshold=True, end_cycles=3)
        session["below_threshold_cycles"] = cycles
    assert session["below_threshold_cycles"] == 2
    session["below_threshold_cycles"] = 0  # mirrors _control_tick's direct reset on a discharge run
    for _ in range(2):
        ended, cycles = _decide_car_session_end(session, car_below_threshold=True, end_cycles=3)
        assert ended is False
        session["below_threshold_cycles"] = cycles
    assert session["below_threshold_cycles"] == 2  # counting from 0 again, not resuming from 2


def test_any_other_end_reason_bypasses_hysteresis_and_ends_immediately():
    # car_below_threshold=False = the car is STILL reading above threshold but the session is
    # ending for some other reason (reserve floor, master switch off) — never delayed (safety).
    session = {"below_threshold_cycles": 1}  # even mid-grace, this must end on the spot
    ended, cycles = _decide_car_session_end(session, car_below_threshold=False, end_cycles=3)
    assert (ended, cycles) == (True, 0)


def test_default_end_cycles_setting_is_honoured_by_the_pure_gate():
    # control.car_session_end_cycles defaults to 3 — the SAME schema default _car_session_end_if_
    # active reads via settings_cache, exercised here through the pure gate (no hardcoded "3").
    end_cycles = int(settings_defaults()["control.car_session_end_cycles"])
    assert end_cycles == 3
    session = {"below_threshold_cycles": 0}
    for _ in range(end_cycles - 1):
        ended, cycles = _decide_car_session_end(
            session, car_below_threshold=True, end_cycles=end_cycles)
        assert ended is False
        session["below_threshold_cycles"] = cycles
    ended, _cycles = _decide_car_session_end(
        session, car_below_threshold=True, end_cycles=end_cycles)
    assert ended is True


def test_end_cycles_one_reproduces_the_original_immediate_end_behaviour():
    # Setting the knob to 1 is documented as "today's original behaviour" — a single below-
    # threshold cycle must end the session right away, same as before this fix existed.
    session = {"below_threshold_cycles": 0}
    ended, cycles = _decide_car_session_end(session, car_below_threshold=True, end_cycles=1)
    assert (ended, cycles) == (True, 0)


# ==================================================================================================
# 4. Guard substitution + regression pins, end-to-end through /api/decision
# ==================================================================================================

class _CarSource:
    """Read-only source with a settable EV power + SoC (the rest benign)."""

    def __init__(self, ev_w: float, soc: float = 55.0) -> None:
        self.ev_w, self.soc = ev_w, soc

    def read(self) -> RawSample:
        return RawSample(grid_power_w=0.0, solar_power_w=0.0, battery_power_w=0.0,
                         ev_power_w=self.ev_w, soc_pct=self.soc)


class _FlatPrices:
    """Flat prices -> no arbitrage trade -> the plan is plain self-consumption."""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        self._slots = [PriceSlot(base + i * SLOT, 0.25) for i in range(-2, 96)]

    def slots(self) -> list[PriceSlot]:
        return self._slots


def _fresh_tracker() -> FreshnessTracker:
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for s in SIGNALS:
        fr.mark(s, now)
    return fr


def _decision_app(tmp_path, ev_w: float, *, fresh: bool = True):
    db = str(tmp_path / "ems.sqlite")
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    return create_app(
        _CarSource(ev_w), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=_FlatPrices(), solar_forecast=MockSolarForecastSource(AMS),
        controller=controller, settings_store=SettingsStore(db),
        freshness=_fresh_tracker() if fresh else None,
    )


def _decision(c, mode: str, **extra):
    c.post("/api/settings", json={"strategy.mode": "winter",
                                  "control.car_charging_battery_mode": mode, **extra})
    return c.get("/api/decision").json()


def test_default_hold_mode_is_todays_behaviour_byte_for_byte(tmp_path):
    # Regression pin: the DEFAULT ("hold") is exactly the pre-feature car-guard.
    with TestClient(_decision_app(tmp_path, ev_w=3000.0)) as c:
        b = _decision(c, "hold")
    assert b["car_charging"] is True
    assert b["intent"] == "hold_reserve"
    assert b["desired_mode"] == "idle"
    assert "car charging" in b["plan_reason"]
    assert "won't discharge into the car" in b["plan_reason"]


def test_static_discharge_mode_commands_a_real_discharge(tmp_path):
    with TestClient(_decision_app(tmp_path, ev_w=3000.0)) as c:
        b = _decision(c, "static_discharge", **{"control.car_discharge_w": 1200})
    assert b["car_charging"] is True
    assert b["intent"] == "discharge_for_load"
    assert b["desired_mode"] == "discharge"       # a REAL discharge (car_session mapping), not AUTO
    assert "1200" in b["plan_reason"]             # the honest, sized reason
    assert b["target_soc"] == 10.0                # the reserve floor is the stop


def test_match_home_load_mode_covers_the_house(tmp_path):
    with TestClient(_decision_app(tmp_path, ev_w=3000.0)) as c:
        b = _decision(c, "match_home_load")
    assert b["intent"] == "discharge_for_load"
    assert b["desired_mode"] == "discharge"
    assert "covering the house" in b["plan_reason"]


def test_reserve_floor_holds_a_discharge_mode_instead_of_draining(tmp_path):
    # Inviolable reserve floor: at/near the reserve SoC a discharge mode HOLDS (grid covers the car
    # and the house). This is what ends a session on a drained battery mid-charge.
    db = str(tmp_path / "ems.sqlite")
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    app = create_app(
        _CarSource(3000.0, soc=10.5), dry_run=True, dev_mode="mock", tz=AMS,  # reserve default 10
        price_source=_FlatPrices(), solar_forecast=MockSolarForecastSource(AMS),
        controller=controller, settings_store=SettingsStore(db), freshness=_fresh_tracker(),
    )
    with TestClient(app) as c:
        b = _decision(c, "match_home_load")
    assert b["intent"] == "hold_reserve"
    assert b["desired_mode"] == "idle"
    assert "reserve" in b["plan_reason"]


def test_master_switch_off_runs_the_plan_untouched_even_in_a_discharge_mode(tmp_path):
    # Regression pin: master OFF => the planner runs exactly as with no car, whatever the mode is.
    with TestClient(_decision_app(tmp_path, ev_w=3000.0)) as c:
        b = _decision(c, "static_discharge",
                      **{"control.hold_battery_when_car_charging": False})
    assert b["car_charging"] is True
    assert b["intent"] == "allow_self_consumption"  # untouched


def test_unsafe_data_holds_instead_of_discharging(tmp_path):
    # Fail-safe: an untrusted SoC must NEVER drive a discharge — hold, like the pre-feature guard.
    with TestClient(_decision_app(tmp_path, ev_w=3000.0, fresh=False)) as c:
        b = _decision(c, "static_discharge", **{"control.car_discharge_w": 1200})
    assert b["intent"] == "hold_reserve"
    assert b["desired_mode"] == "idle"
    assert "unsafe" in b["plan_reason"]


# ==================================================================================================
# 5. Operational integration — the REAL control-tick closure drives the battery + audits (live loop)
# ==================================================================================================

def _operational_car_app(tmp_path, source, driver):
    db = str(tmp_path / "ems.sqlite")
    ctl = ModeController(driver, Lifecycle(dry_run=False, startup_grace_seconds=0), dry_run=False)
    app = create_app(
        source, dry_run=False, dev_mode="live", tz=AMS,
        price_source=_FlatPrices(), solar_forecast=MockSolarForecastSource(AMS),
        controller=ctl, freshness=_fresh_tracker(), store=HistoryStore(db),
        settings_store=SettingsStore(db), override_store=SettingsStore(db, table="runtime_state"),
        audit_store=AuditStore(db), control_cycle_seconds=0.02,
    )
    return app


def _audit_summaries(c, needle):
    return [e for e in c.get("/api/audit").json()["entries"]
            if e["category"] == "battery_decision" and needle in e["summary"]]


def _wait(cond, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not cond():
        time.sleep(0.05)


def test_live_control_loop_runs_a_car_session_and_stays_quiet(tmp_path):
    driver = _CountingDriver()  # starts AUTO, counts apply()
    with TestClient(_operational_car_app(tmp_path, _CarSource(3000.0), driver)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter",
                                      "control.car_charging_battery_mode": "static_discharge",
                                      "control.car_discharge_w": 900})
        _wait(lambda: driver.current_mode() is PhysicalMode.DISCHARGE)
        assert driver.current_mode() is PhysicalMode.DISCHARGE  # the REAL closure discharged
        calls_at_discharge = driver.apply_calls
        time.sleep(0.4)  # many further cycles with a stable prediction
        quiet_calls = driver.apply_calls
        audits = _audit_summaries(c, "car session")
    assert audits, "the car session must be audited"
    # Stable prediction + the 10-min car dwell => no re-commands: the write count does not grow.
    assert quiet_calls == calls_at_discharge


def test_live_session_ends_and_resumes_the_plan_when_the_car_condition_clears(tmp_path):
    # The car session ends (audited) and the battery resumes the plan (AUTO self-consumption under
    # flat prices), exactly like today's car-guard hold releasing. We end it by flipping the master
    # switch off — an immediate settings change, so the transition is deterministic within the test
    # window (a live EV-power change is only re-read on the coalescing interval, 15-60 s). The same
    # tick path fires when the car actually stops (`_car_charging` False → car-mode dormant).
    driver = _CountingDriver()
    with TestClient(_operational_car_app(tmp_path, _CarSource(3000.0), driver)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter",
                                      "control.car_charging_battery_mode": "static_discharge",
                                      "control.car_discharge_w": 900})
        _wait(lambda: driver.current_mode() is PhysicalMode.DISCHARGE)
        assert driver.current_mode() is PhysicalMode.DISCHARGE
        c.post("/api/settings", json={"control.hold_battery_when_car_charging": False})
        _wait(lambda: driver.current_mode() is PhysicalMode.AUTO)
        assert driver.current_mode() is PhysicalMode.AUTO  # plan resumed (self-consumption)
        ended = _audit_summaries(c, "car session ended")
    assert ended, "the session end must be audited"
