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
from ems.planner.strategy import HysteresisState
from ems.sense import SIGNALS
from ems.settings import defaults as settings_defaults
from ems.sources.battery import BatteryWriteUnconfirmed, MockBatteryDriver, intent_to_mode
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import MockPriceSource, PriceSlot
from ems.storage.audit import AuditStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import (
    _CAR_SESSION_MAX_COMMANDS,
    _commit_hysteresis_state,
    _decide_car_command,
    _decide_car_session_end,
    _decide_grace_action,
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


# ==================================================================================================
# 3a. F1 — observed-mode reconciliation (the CRITICAL finding)
#
# A car-session discharge whose FIRST write returns 'unconfirmed' advances the session box as if it
# applied; with a stable prediction `recommand` never fires again, so the battery sat in vendor AUTO
# draining into the car for the whole session. Fix: each cycle the command decision ALSO considers
# the OBSERVED battery mode (from the shared coalesced read, never a fresh device read) and the last
# command's outcome — if the session is active but the battery isn't actually DISCHARGE (or the last
# write was unconfirmed/failed), a re-command is DUE on a short (>= 1 cycle) reconciliation spacing,
# still bounded by the 6-command session cap.
# ==================================================================================================

_RECON_SPACING = timedelta(seconds=300)


def _recon_session(setpoint, commanded_at, *, commands=1, last_outcome="applied") -> dict:
    return {"active": True, "setpoint_w": setpoint, "commanded_at": commanded_at.isoformat(),
            "commands": commands, "last_outcome": last_outcome}


def test_unconfirmed_first_write_re_commands_next_cycle_despite_stable_prediction():
    # First write returned 'unconfirmed' -> the recorded DISCHARGE setpoint may never have taken.
    # The prediction is stable (recommand=False), so ONLY the reconciliation path re-commands.
    session = _recon_session(800.0, T0, last_outcome="unconfirmed")
    cmd, nxt, event = _decide_car_command(
        session, _disc(800.0, False), T0 + _RECON_SPACING,
        observed_mode=PhysicalMode.AUTO, reconcile_spacing=_RECON_SPACING)
    assert cmd is True and event == "reconcile"
    assert nxt["commands"] == 2 and nxt["setpoint_w"] == 800.0


def test_observed_auto_despite_recorded_setpoint_re_commands():
    # Last write 'applied' but the battery is OBSERVED in AUTO (vendor drifted / never actually took
    # the setpoint). Stable prediction -> the observed-mode reconciliation re-commands.
    session = _recon_session(800.0, T0, last_outcome="applied")
    cmd, _nxt, event = _decide_car_command(
        session, _disc(800.0, False), T0 + _RECON_SPACING,
        observed_mode=PhysicalMode.AUTO, reconcile_spacing=_RECON_SPACING)
    assert cmd is True and event == "reconcile"


def test_confirmed_and_observed_discharge_stays_quiet():
    # Happy path: last write applied, observed DISCHARGE, stable prediction -> HOLD, no write.
    session = _recon_session(800.0, T0 - timedelta(minutes=30), last_outcome="applied")
    cmd, _nxt, event = _decide_car_command(
        session, _disc(850.0, False), T0,
        observed_mode=PhysicalMode.DISCHARGE, reconcile_spacing=_RECON_SPACING)
    assert cmd is False and event == "hold"


def test_reconcile_respects_the_short_retry_spacing_not_the_ten_minute_dwell():
    # The reconcile re-command is spaced by >= 1 cycle (reconcile_spacing), NOT the 10-min dwell.
    session = _recon_session(800.0, T0, last_outcome="unconfirmed")
    early = _decide_car_command(session, _disc(800.0, False), T0 + timedelta(seconds=200),
                                observed_mode=PhysicalMode.AUTO, reconcile_spacing=_RECON_SPACING)
    assert early[0] is False and early[2] == "hold"          # within one cycle -> hold
    late = _decide_car_command(session, _disc(800.0, False), T0 + timedelta(seconds=300),
                               observed_mode=PhysicalMode.AUTO, reconcile_spacing=_RECON_SPACING)
    assert late[0] is True and late[2] == "reconcile"        # one cycle elapsed -> reconcile


def test_reconcile_cap_exhaustion_signals_the_hold_fallback():
    # The reconcile re-command still counts toward the 6-command cap. Once spent, the pure gate
    # signals 'cap_reconcile' so the caller falls back to the safe HOLD (idle) path — never keeps
    # holding a discharge setpoint the battery never actually adopted.
    session = _recon_session(800.0, T0, commands=_CAR_SESSION_MAX_COMMANDS,
                             last_outcome="unconfirmed")
    cmd, _nxt, event = _decide_car_command(
        session, _disc(800.0, False), T0 + _RECON_SPACING,
        observed_mode=PhysicalMode.AUTO, reconcile_spacing=_RECON_SPACING)
    assert cmd is False and event == "cap_reconcile"


def test_no_reconcile_when_observed_mode_is_unknown_and_last_outcome_good():
    # observed_mode None (device unreadable this cycle) must NOT trigger a spurious reconcile when
    # the last write was fine — we don't KNOW it drifted, so we stay quiet (avoids a flood).
    session = _recon_session(800.0, T0 - timedelta(minutes=30), last_outcome="applied")
    cmd, _nxt, event = _decide_car_command(
        session, _disc(850.0, False), T0,
        observed_mode=None, reconcile_spacing=_RECON_SPACING)
    assert cmd is False and event == "hold"


# ==================================================================================================
# 3c. F3 / F5 — grace-window action (override / fail-safe / reserve floor must act THIS cycle)
# ==================================================================================================

def test_grace_override_falls_through_this_cycle():
    # F3: a manual override lands during the below-threshold grace window -> end + apply now.
    assert _decide_grace_action(
        override_active=True, failsafe=False, soc_pct=55.0, min_reserve_soc=10.0) == "fall_through"


def test_grace_failsafe_falls_through_this_cycle():
    # F3: a data-quality fail-safe during grace -> end + apply the fail-safe now, don't hold.
    assert _decide_grace_action(
        override_active=False, failsafe=True, soc_pct=55.0, min_reserve_soc=10.0) == "fall_through"


def test_grace_reserve_floor_holds_now():
    # F5: SoC at the reserve floor during grace -> end + hold at reserve now (don't keep discharging
    # through the grace window on a drained battery).
    assert _decide_grace_action(
        override_active=False, failsafe=False, soc_pct=10.5, min_reserve_soc=10.0) == "reserve_hold"


def test_grace_benign_blip_holds_the_session():
    # A pure car-power blip (no override, no fail-safe, SoC well above the floor) -> hold the
    # setpoint through the grace window (unchanged behaviour — the whole point of the grace window).
    assert _decide_grace_action(
        override_active=False, failsafe=False, soc_pct=55.0, min_reserve_soc=10.0) == "hold"


def test_grace_override_takes_precedence_over_the_reserve_floor():
    # A deliberate operator override wins even at the floor (they see the badges).
    assert _decide_grace_action(
        override_active=True, failsafe=False, soc_pct=10.0, min_reserve_soc=10.0) == "fall_through"


# ==================================================================================================
# 3d. F6 — hysteresis-box commit is serialised across threads
# ==================================================================================================

def test_commit_hysteresis_state_serialises_concurrent_writers():
    # _commit_hysteresis does a read-modify-write on the shared box + a cache_store.set from three
    # unsynchronised threads (periodic loop, override-triggered cycle, dashboard read). A lock must
    # serialise the RMW+persist so no writer's persist is lost or interleaved.
    import threading

    box = {"state": HysteresisState()}
    lock = threading.Lock()

    class _RacyCache:
        def __init__(self) -> None:
            self.sets = 0
            self._inside = 0
            self.max_concurrent = 0

        def set(self, key, value, ttl):
            self._inside += 1
            self.max_concurrent = max(self.max_concurrent, self._inside)
            time.sleep(0.002)  # widen the race window
            self.sets += 1
            self._inside -= 1

    cache = _RacyCache()
    states = [HysteresisState(committed="winter", pending="summer", count=i, last_day=None)
              for i in range(1, 21)]
    threads = [threading.Thread(target=_commit_hysteresis_state,
                                args=(box, lock, s, cache, "k", 1.0)) for s in states]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert cache.sets == 20            # every writer persisted; none lost
    assert cache.max_concurrent == 1   # the lock serialised them (no overlapping set())


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
        self._slots = [PriceSlot(slot.start, 0.25) for slot in MockPriceSource(AMS).slots()]

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


class _UnconfirmFirstDischargeDriver(MockBatteryDriver):
    """AUTO to start; the FIRST DISCHARGE write times out (BatteryWriteUnconfirmed) so the battery
    stays AUTO with an 'unconfirmed' outcome — the exact live failure mode of F1. Every later write
    (including the reconciliation re-command) is accepted normally."""

    def __init__(self) -> None:
        super().__init__()
        self.discharge_writes = 0

    def apply(self, mode, *, target_soc=None, power_w=None):
        if mode is PhysicalMode.DISCHARGE:
            self.discharge_writes += 1
            if self.discharge_writes == 1:
                raise BatteryWriteUnconfirmed("first discharge write timed out")
        return super().apply(mode, target_soc=target_soc, power_w=power_w)


def test_live_unconfirmed_first_write_reconciles_to_discharge(tmp_path):
    # F1 (CRITICAL): the first discharge write is unconfirmed, so the battery is still in vendor
    # AUTO draining into the car. With a stable prediction the ordinary re-command never fires — the
    # observed-mode reconciliation rescues it. The battery MUST reach a real DISCHARGE, and it takes
    # at least a second (reconciling) discharge write to get there.
    driver = _UnconfirmFirstDischargeDriver()
    with TestClient(_operational_car_app(tmp_path, _CarSource(3000.0), driver)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter",
                                      "control.car_charging_battery_mode": "static_discharge",
                                      "control.car_discharge_w": 900})
        _wait(lambda: driver.current_mode() is PhysicalMode.DISCHARGE)
        assert driver.current_mode() is PhysicalMode.DISCHARGE  # reconciled off vendor AUTO
        assert driver.discharge_writes >= 2  # first unconfirmed; reconciliation re-commanded


def test_live_reserve_hold_keeps_the_session_alive_and_resumes(tmp_path):
    # F2: SoC hitting the reserve floor mid-session must HOLD (idle) WITHOUT ending+restarting the
    # session, and resume discharging once it recovers past the +3pp hysteresis band. The live SoC
    # is coalesced (>= 15 s), so we move the FLOOR instead of the SoC — battery.min_reserve_soc is
    # read fresh from settings every cycle. SoC is a fixed 45%: floor 10 -> discharge (45 > 11);
    # floor 45 -> reserve hold (45 <= 46, and 45 < 48 keeps it held); floor 10 -> resume (45 >= 13).
    driver = _CountingDriver()
    with TestClient(_operational_car_app(tmp_path, _CarSource(3000.0, soc=45.0), driver)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter",
                                      "control.car_charging_battery_mode": "static_discharge",
                                      "control.car_discharge_w": 900})
        _wait(lambda: driver.current_mode() is PhysicalMode.DISCHARGE)
        assert driver.current_mode() is PhysicalMode.DISCHARGE
        # Raise the reserve floor to SoC's band -> the battery idles (reserve hold), session HELD.
        c.post("/api/settings", json={"battery.min_reserve_soc": 45.0})
        _wait(lambda: driver.current_mode() is PhysicalMode.IDLE)
        assert driver.current_mode() is PhysicalMode.IDLE
        held = _audit_summaries(c, "held at the reserve floor")
        # Lower the floor again -> recovery past the +3pp band -> discharge RESUMES (same session).
        c.post("/api/settings", json={"battery.min_reserve_soc": 10.0})
        _wait(lambda: driver.current_mode() is PhysicalMode.DISCHARGE)
        assert driver.current_mode() is PhysicalMode.DISCHARGE
        ended = _audit_summaries(c, "car session ended")
    assert held, "the reserve-floor hold must be audited"
    assert not ended, "the session must SURVIVE the reserve hold (never ended+restarted)"
