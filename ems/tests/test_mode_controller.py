import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import (
    BatteryWriteUnconfirmed,
    FailingMockBatteryDriver,
    MockBatteryDriver,
)

T0 = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)


def _controlling_lifecycle():
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(T0 + timedelta(seconds=121))  # -> CONTROLLING
    return lc


def test_dry_run_never_writes():
    d = MockBatteryDriver()
    ctl = ModeController(d, Lifecycle(dry_run=True), dry_run=True)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0)
    assert dec.outcome == "dry_run"
    assert dec.applied is False
    assert d.current_mode() is PhysicalMode.AUTO  # untouched


def test_not_controlling_does_not_write():
    d = MockBatteryDriver()
    ctl = ModeController(d, Lifecycle(dry_run=False), dry_run=False)  # never started -> INACTIVE
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0)
    assert dec.outcome == "not_controlling"
    assert d.current_mode() is PhysicalMode.AUTO


def test_applies_when_controlling_and_mode_differs():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    now = T0 + timedelta(seconds=200)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, now)
    assert dec.outcome == "applied"
    assert dec.applied is True
    assert d.current_mode() is PhysicalMode.CHARGE


def test_idempotent_when_already_in_mode():
    d = MockBatteryDriver()  # starts AUTO
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.ALLOW_SELF_CONSUMPTION, T0 + timedelta(seconds=200))
    assert dec.outcome == "idempotent"
    assert dec.applied is False


def test_min_dwell_blocks_rapid_switch():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, min_dwell_seconds=600)
    t1 = T0 + timedelta(seconds=200)
    ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1)  # applied, last_switch=t1
    dec = ctl.decide(BatteryIntent.HOLD_RESERVE, t1 + timedelta(seconds=60))  # within dwell
    assert dec.outcome == "dwell"
    assert dec.applied is False


def test_daily_switch_cap_holds():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=0)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))
    assert dec.outcome == "cap_reached"
    assert dec.applied is False


# F4 — a car-session (priority) command must NOT consume the planner's daily switch budget --------

def test_count_toward_cap_false_leaves_the_switch_counter_untouched():
    # A car-session command is a priority write that bypasses the cap; before F4 it still bumped
    # switches_today, starving the planner's daily budget. count_toward_cap=False fixes that.
    d = MockBatteryDriver()  # starts AUTO
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, min_dwell_seconds=0)
    # An ordinary planner write establishes the counter at 1 (and stamps last_switch_at).
    ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200), target_soc=80.0)
    assert ctl.switches_today == 1
    switch_at_after_ordinary = ctl.last_switch_at
    # Now a car-session command (a real DISCHARGE) — it must NOT spend the planner's budget.
    dec = ctl.decide(BatteryIntent.DISCHARGE_FOR_LOAD, T0 + timedelta(seconds=400),
                     target_soc=10.0, power_w=800.0, car_session=True, force=True,
                     priority=True, count_toward_cap=False)
    assert dec.outcome == "applied"
    assert d.current_mode() is PhysicalMode.DISCHARGE
    assert ctl.switches_today == 1                      # untouched — the planner keeps its budget
    assert ctl.last_switch_at == switch_at_after_ordinary  # and the dwell timer isn't disturbed


def test_count_toward_cap_false_on_unconfirmed_still_leaves_the_counter_untouched():
    # The same holds when a car command TIMES OUT (unconfirmed) — its retries are spaced by the car
    # session's own cadence, not the planner's dwell/cap, so it must not touch switches_today.
    class TimingOut(MockBatteryDriver):
        def apply(self, mode, *, target_soc=None, power_w=None):
            raise BatteryWriteUnconfirmed("timed out")

    ctl = ModeController(TimingOut(), _controlling_lifecycle(), dry_run=False)
    ctl._reset_counter_if_new_day(T0 + timedelta(seconds=200))  # seed the counter date for today
    ctl.switches_today = 4
    dec = ctl.decide(BatteryIntent.DISCHARGE_FOR_LOAD, T0 + timedelta(seconds=200),
                     target_soc=10.0, power_w=800.0, car_session=True, force=True,
                     priority=True, count_toward_cap=False)
    assert dec.outcome == "unconfirmed"
    assert ctl.switches_today == 4


def test_ordinary_writes_still_count_and_cap():
    # Regression pin: an ORDINARY planner write (count_toward_cap defaults True) still increments
    # the counter and is still capped once the daily budget is spent.
    d = MockBatteryDriver()  # starts AUTO
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False,
                         max_switches_per_day=1, min_dwell_seconds=0)
    ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200), target_soc=80.0)
    assert ctl.switches_today == 1
    dec = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(seconds=400))  # AUTO->CHARGE->IDLE
    assert dec.outcome == "cap_reached"
    assert dec.applied is False


def test_failed_apply_recovers_to_auto():
    d = FailingMockBatteryDriver(fail_times=1)
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))
    assert dec.outcome == "failed_recovered"
    assert dec.desired_mode is PhysicalMode.AUTO
    assert d.current_mode() is PhysicalMode.AUTO  # recovered to safe mode


def test_failed_apply_and_failed_recovery_is_flagged():
    d = FailingMockBatteryDriver(fail_times=2)  # both the apply AND the AUTO recovery fail
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))
    assert dec.outcome == "failed_unrecovered"
    assert dec.applied is False


def test_transport_timeout_holds_and_does_not_revert_to_auto():
    # A write that times out (BatteryWriteUnconfirmed) must NOT trigger the AUTO revert: the device
    # is slow and very likely received the command. Reverting fires another write that also times
    # out (the live ALERT spiral) and loses the charge. So: hold the intent, count it, retry later.
    class TimingOutDriver(MockBatteryDriver):
        def apply(self, mode, *, target_soc=None, power_w=None):
            raise BatteryWriteUnconfirmed("timed out")

    d = TimingOutDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200),
                     target_soc=100, manual=True)
    assert dec.outcome == "unconfirmed"
    assert dec.desired_mode is PhysicalMode.CHARGE   # NOT reverted to AUTO
    assert ctl.switches_today == 1                    # counted so automatic retries are spaced
    assert ctl.last_switch_at == T0 + timedelta(seconds=200)


def test_genuine_rejection_still_reverts_to_auto():
    # Regression guard: a genuine REJECTION (apply returns False, not a timeout) still reverts.
    d = FailingMockBatteryDriver(fail_times=1)  # apply() returns False (rejection), never raises
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200),
                     target_soc=100, manual=True)
    assert dec.outcome == "failed_recovered"
    assert dec.desired_mode is PhysicalMode.AUTO


def test_preview_never_writes_even_when_controlling():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.preview(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))
    assert dec.outcome == "would_apply"
    assert dec.applied is False
    assert d.current_mode() is PhysicalMode.AUTO  # NOT written
    assert ctl.switches_today == 0  # NOT mutated


class _CountingDriver(MockBatteryDriver):
    """Counts how many times the device is asked for its current mode."""

    def __init__(self) -> None:
        super().__init__()
        self.mode_reads = 0

    def current_mode(self) -> PhysicalMode:
        self.mode_reads += 1
        return super().current_mode()


def test_decide_uses_observed_mode_without_reading_the_device():
    # The control loop passes a recently-observed mode (from the shared coalesced cluster read) so
    # the idempotency check doesn't add a master mode-read every cycle. Already-in-mode → idempotent
    # with ZERO device reads.
    d = _CountingDriver()  # starts AUTO
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.ALLOW_SELF_CONSUMPTION, T0 + timedelta(seconds=200),
                     observed_mode=PhysicalMode.AUTO)
    assert dec.outcome == "idempotent"
    assert d.mode_reads == 0  # the device was NOT polled for the idempotency check


def test_decide_reads_the_device_when_no_observed_mode_given():
    d = _CountingDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    ctl.decide(BatteryIntent.ALLOW_SELF_CONSUMPTION, T0 + timedelta(seconds=200))
    assert d.mode_reads >= 1  # falls back to a fresh device read (prior behaviour)


def test_failed_write_starts_dwell_and_counts_so_it_cannot_retry_every_cycle():
    # A write that never confirms (e.g. a half-offline tower) must NOT be re-attempted every
    # control cycle — that is write-amplification into struggling hardware. A failed attempt starts
    # the dwell timer and counts toward the daily cap, exactly like a confirmed switch.
    d = FailingMockBatteryDriver(fail_times=99)  # every apply() fails to confirm
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, min_dwell_seconds=600)
    t1 = T0 + timedelta(seconds=200)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1)
    assert dec.outcome in ("failed_recovered", "failed_unrecovered")
    assert ctl.switches_today == 1          # the failed attempt counted toward the cap
    assert ctl.last_switch_at == t1         # ...and started the dwell timer
    # The next control cycle (5 min later, inside the 10 min dwell) is BLOCKED, not retried.
    nxt = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1 + timedelta(seconds=300))
    assert nxt.outcome == "dwell"
    assert nxt.applied is False


def test_repeated_failed_writes_are_bounded_by_the_daily_cap():
    # Even spaced past the dwell, a never-confirming write can't run forever — the cap stops it.
    d = FailingMockBatteryDriver(fail_times=99)
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False,
                         min_dwell_seconds=600, max_switches_per_day=3)
    t = T0 + timedelta(seconds=200)
    for _ in range(3):
        dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t)
        assert dec.outcome in ("failed_recovered", "failed_unrecovered")
        t += timedelta(seconds=700)  # clear the 600 s dwell each iteration
    assert ctl.switches_today == 3
    capped = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t)
    assert capped.outcome == "cap_reached"
    assert capped.applied is False


def test_manual_override_bypasses_daily_cap():
    # An explicit operator override must NOT be silently vetoed by the daily switch cap (which
    # limits AUTOMATIC churn). This was the live "manual charge does nothing" bug: a day of testing
    # exhausted the cap, then every "charge now" was blocked with no audit and no write.
    d = MockBatteryDriver()  # starts AUTO
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=0)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200),
                     target_soc=100, manual=True)
    assert dec.outcome == "applied"
    assert d.current_mode() is PhysicalMode.CHARGE


def test_manual_override_bypasses_min_dwell():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, min_dwell_seconds=600)
    t1 = T0 + timedelta(seconds=200)
    ctl.decide(BatteryIntent.ALLOW_SELF_CONSUMPTION, t1)  # establishes last_switch (no-op AUTO ok)
    ctl.last_switch_at = t1  # force a recent switch
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1 + timedelta(seconds=60),
                     target_soc=100, manual=True)  # well within dwell
    assert dec.outcome == "applied"
    assert d.current_mode() is PhysicalMode.CHARGE


def test_return_to_auto_always_allowed_even_when_capped():
    # The fail-safe (return to vendor self-consumption) must never be blocked by the cap — else an
    # expiring override could leave the battery stuck charging.
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=1)
    t = T0 + timedelta(seconds=200)
    ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t, target_soc=100, manual=True)  # now CHARGE
    assert d.current_mode() is PhysicalMode.CHARGE
    back = ctl.decide(BatteryIntent.ALLOW_SELF_CONSUMPTION, t + timedelta(seconds=60))  # automatic
    assert back.outcome == "applied"  # AUTO bypasses the cap/dwell
    assert d.current_mode() is PhysicalMode.AUTO


def test_manual_override_still_idempotent_no_double_write():
    # Bypassing the cap/dwell must NOT defeat idempotency — a manual charge while already charging
    # is still a no-op (the control loop won't hammer the device every cycle).
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.ALLOW_SELF_CONSUMPTION, T0 + timedelta(seconds=200),
                     observed_mode=PhysicalMode.AUTO, manual=True)
    assert dec.outcome == "idempotent"


def test_priority_safety_hold_bypasses_daily_cap():
    # A safety hold (car-guard, priority=True) must apply even with the daily cap exhausted — the
    # live bug: a capped IDLE left the battery self-consuming into a 10 kW car. HOLD_RESERVE → IDLE.
    d = MockBatteryDriver()  # starts AUTO
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=0)
    dec = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(seconds=200), priority=True)
    assert dec.outcome == "applied"
    assert d.current_mode() is PhysicalMode.IDLE


def test_priority_safety_hold_bypasses_min_dwell():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, min_dwell_seconds=600)
    t1 = T0 + timedelta(seconds=200)
    ctl.last_switch_at = t1  # a recent switch
    dec = ctl.decide(BatteryIntent.HOLD_RESERVE, t1 + timedelta(seconds=60), priority=True)
    assert dec.outcome == "applied"  # within dwell, but a safety hold isn't gated
    assert d.current_mode() is PhysicalMode.IDLE


def test_automatic_switch_is_still_capped():
    # Regression guard: WITHOUT manual, the daily cap still limits the automatic planner.
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=0)
    dec = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(seconds=200))
    assert dec.outcome == "cap_reached"
    assert dec.applied is False


def test_switch_cap_resets_on_a_new_local_day():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=1)
    ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))  # switch 1/1
    blocked = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(seconds=900))  # same day
    assert blocked.outcome == "cap_reached"
    next_day = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(days=1, seconds=200))
    assert next_day.outcome == "applied"  # counter reset at the new local date


def test_switch_cap_window_rolls_at_local_midnight_not_utc():
    # The daily switch cap resets at LOCAL midnight (the documented contract), NOT UTC. In Amsterdam
    # summer (CEST = UTC+2) 23:30 UTC is 01:30 the NEXT local day, so a switch at that instant
    # belongs to that local day; a later switch on the SAME local day is still capped, and only
    # once local midnight passes does the counter reset. (Under a UTC day-boundary the second switch
    # would land on a new UTC date and wrongly reset the cap early — the main.py wiring bug.)
    ams = ZoneInfo("Europe/Amsterdam")
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=1, tz=ams)

    at_2330_utc = datetime(2026, 7, 1, 23, 30, tzinfo=UTC)     # 01:30 CEST, local day = Jul 2
    first = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, at_2330_utc)
    assert first.outcome == "applied"                           # 1/1 for local day Jul 2

    same_local_day = datetime(2026, 7, 2, 21, 0, tzinfo=UTC)    # 23:00 CEST, still local day Jul 2
    blocked = ctl.decide(BatteryIntent.HOLD_RESERVE, same_local_day)
    assert blocked.outcome == "cap_reached"                     # belongs to the SAME local day

    after_local_midnight = datetime(2026, 7, 2, 22, 30, tzinfo=UTC)  # 00:30 CEST, new local day
    reset = ctl.decide(BatteryIntent.HOLD_RESERVE, after_local_midnight)
    assert reset.outcome == "applied"                           # counter reset at local midnight


class _StuckDriver(MockBatteryDriver):
    """A driver whose apply() times out (BatteryWriteUnconfirmed) on demand, and always reports its
    mode as AUTO so a CHARGE intent never short-circuits as idempotent — lets us drive an intent
    that stays stuck across cycles and then recovers."""

    def __init__(self) -> None:
        super().__init__()
        self.stuck = True

    def current_mode(self) -> PhysicalMode:
        return PhysicalMode.AUTO  # never idempotent for a CHARGE/IDLE intent

    def apply(self, mode, *, target_soc=None, power_w=None):
        if self.stuck:
            raise BatteryWriteUnconfirmed("device slow")
        return super().apply(mode, target_soc=target_soc, power_w=power_w)


def test_stuck_unconfirmed_episode_audits_once_across_cycles():
    # One real "charge isn't sticking (device slow)" episode must produce ONE audit-worthy row, not
    # a row every dwell cycle (the live 13-row inflation). manual=True bypasses dwell so all five
    # cycles actually re-attempt the write and hit the unconfirmed branch.
    d = _StuckDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    audits = 0
    for i in range(5):
        dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200 + i),
                         target_soc=90, manual=True)
        assert dec.outcome == "unconfirmed"  # control behaviour (HOLD) unchanged every cycle
        audits += int(dec.audit)
    assert audits == 1  # exactly one incident row for the whole episode


def test_recovery_then_restick_audits_a_second_row():
    d = _StuckDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    first = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200),
                       target_soc=90, manual=True)
    assert first.audit is True
    again = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=201),
                       target_soc=90, manual=True)
    assert again.audit is False  # suppressed within the same episode
    # The device recovers: the write confirms → episode ends.
    d.stuck = False
    ok = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=202),
                    target_soc=90, manual=True)
    assert ok.outcome == "applied"
    # ...then it re-sticks: a fresh episode → a NEW audit row.
    d.stuck = True
    restick = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=203),
                         target_soc=90, manual=True)
    assert restick.outcome == "unconfirmed"
    assert restick.audit is True


def test_long_outage_relogs_after_61_minutes():
    # A persistent outage still leaves periodic evidence: re-log once >60 min have passed since the
    # episode was last logged, so hours of "not sticking" isn't a single stale row.
    d = _StuckDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    t1 = T0 + timedelta(seconds=200)
    first = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1, target_soc=90, manual=True)
    assert first.audit is True
    mid = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1 + timedelta(minutes=30),
                     target_soc=90, manual=True)
    assert mid.audit is False  # still within the hour since the first log → suppressed
    later = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1 + timedelta(minutes=61),
                       target_soc=90, manual=True)
    assert later.audit is True  # >60 min since the last log → re-logged


def test_different_intent_starts_a_new_episode_row():
    # A different (intent, mode) is a distinct episode and audits afresh — even mid-suppression of
    # another one.
    d = _StuckDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    charge = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200),
                        target_soc=90, manual=True)
    assert charge.audit is True
    # HOLD_RESERVE → IDLE: a different desired mode → a new episode, so it audits.
    hold = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(seconds=201), manual=True)
    assert hold.outcome == "unconfirmed"
    assert hold.audit is True


# ==================================================================================================
# Commitment reserve — split the daily switch cap into routine vs commitment budgets so routine
# auto<->idle flapping can never starve a committed grid-charge (the 07-12 guardrail-starvation
# incident: 13 routine flaps burned the 10-switch cap by 09:48, then 5 cap_reached blocks starved a
# COMMITTED grid-charge, which missed its deadline by 66 min). Routine switches may use at most
# (cap - commitment_reserve); a committed grid-charge draws from the full cap; the total is always
# bounded by the cap. In production only GRID_CHARGE_TO_TARGET is flagged commitment=True; these
# unit tests drive the `commitment` flag directly (alternating IDLE/CHARGE to force real writes) to
# exercise the ACCOUNTING, which is intent-agnostic by design.
# ==================================================================================================


def _burn_switches(ctl, driver, n, start, *, commitment):
    """Issue `n` REAL mode switches (alternating IDLE<->CHARGE from wherever we are), each of which
    must apply. Returns the next timestamp. min_dwell must be 0 so they don't self-block."""
    t = start
    for _ in range(n):
        intent = (BatteryIntent.GRID_CHARGE_TO_TARGET if driver.current_mode() is PhysicalMode.IDLE
                  else BatteryIntent.HOLD_RESERVE)  # AUTO/CHARGE -> IDLE ; IDLE -> CHARGE
        dec = ctl.decide(intent, t, target_soc=90, commitment=commitment)
        assert dec.outcome == "applied", (dec.outcome, dec.reason)
        t += timedelta(seconds=1)
    return t


def test_commitment_reserve_carves_out_the_routine_budget():
    d = MockBatteryDriver()  # starts AUTO
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False,
                         max_switches_per_day=10, min_dwell_seconds=0, commitment_reserve=3)
    t = _burn_switches(ctl, d, 7, T0 + timedelta(seconds=200), commitment=False)  # 7 routine
    assert ctl.switches_today == 7
    # The 8th ROUTINE switch is blocked: routine budget (10 - 3 = 7) spent, 3 held for commitments.
    routine = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t, target_soc=90)  # commitment=False
    assert routine.outcome == "cap_reached"
    assert routine.applied is False
    assert "routine switch budget exhausted" in routine.reason
    assert "7/7 used" in routine.reason and "3 reserved" in routine.reason


def test_committed_grid_charge_uses_the_reserve_when_routine_budget_is_spent():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False,
                         max_switches_per_day=10, min_dwell_seconds=0, commitment_reserve=3)
    t = _burn_switches(ctl, d, 7, T0 + timedelta(seconds=200), commitment=False)  # routine spent
    # A COMMITTED grid-charge still goes through — it draws from the reserve (routine leftover + 3).
    commit = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t, target_soc=90, commitment=True)
    assert commit.outcome == "applied"
    assert ctl.switches_today == 8 and ctl.commitment_switches_today == 1


def test_total_daily_writes_never_exceed_the_cap():
    # The reserve carves out headroom; it NEVER extends the cap. cap=10 => at most 10 writes total.
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False,
                         max_switches_per_day=10, min_dwell_seconds=0, commitment_reserve=3)
    t = _burn_switches(ctl, d, 7, T0 + timedelta(seconds=200), commitment=False)  # 7 routine
    t = _burn_switches(ctl, d, 3, t, commitment=True)                             # +3 commitment
    assert ctl.switches_today == 10 and ctl.commitment_switches_today == 3
    # An 11th write of EITHER class is blocked — the daily cap is fully spent.
    over_commit = ctl.decide(BatteryIntent.HOLD_RESERVE, t, commitment=True)   # IDLE, a real switch
    assert over_commit.outcome == "cap_reached"
    over_routine = ctl.decide(BatteryIntent.HOLD_RESERVE, t)                   # commitment=False
    assert over_routine.outcome == "cap_reached"
    assert ctl.switches_today == 10  # nothing slipped past the cap


def test_commitment_reserve_zero_reproduces_the_plain_cap():
    # reserve=0 is the ModeController default (backward compatible): routine may use the WHOLE cap.
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False,
                         max_switches_per_day=3, min_dwell_seconds=0)  # reserve defaults to 0
    _burn_switches(ctl, d, 3, T0 + timedelta(seconds=200), commitment=False)
    assert ctl.switches_today == 3
    blocked = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=400),
                         target_soc=90)
    assert blocked.outcome == "cap_reached"


def test_commitment_and_routine_budgets_reset_on_a_new_local_day():
    ams = ZoneInfo("Europe/Amsterdam")
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=10,
                         min_dwell_seconds=0, commitment_reserve=3, tz=ams)
    t = _burn_switches(ctl, d, 7, T0 + timedelta(seconds=200), commitment=False)
    _burn_switches(ctl, d, 3, t, commitment=True)
    assert ctl.switches_today == 10 and ctl.commitment_switches_today == 3
    # New local day => BOTH counters reset; a routine switch is allowed again from a clean budget.
    nxt = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(days=1, seconds=200))
    assert nxt.outcome == "applied"
    assert ctl.switches_today == 1 and ctl.commitment_switches_today == 0


def test_priority_and_manual_bypass_both_budgets_when_exhausted():
    # Hard invariant (production incident e42828e): manual override / the car-guard safety hold /
    # return-to-AUTO bypass cap+dwell entirely — they must not be routed through EITHER budget nor
    # touch the commitment accounting.
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=10,
                         min_dwell_seconds=0, commitment_reserve=3)
    _burn_switches(ctl, d, 7, T0 + timedelta(seconds=200), commitment=False)  # routine budget spent
    # A safety hold (priority) applies even though the routine budget is exhausted...
    hold = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=400),
                      target_soc=90, priority=True)
    assert hold.outcome == "applied"  # bypassed the budget entirely
    assert ctl.commitment_switches_today == 0  # a priority write is NOT commitment accounting
    # A manual override likewise bypasses the exhausted budget (HOLD_RESERVE -> IDLE).
    manual = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(seconds=500), manual=True)
    assert manual.outcome == "applied"


def test_commitment_switch_count_survives_restart():
    # SPEC §13.3: the split budget must survive a reboot exactly like switches_today does.
    ctl = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    ctl.switches_today = 8
    ctl.commitment_switches_today = 3
    snap = ctl.state_snapshot()
    other = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    other.restore_state(snap)
    assert other.switches_today == 8 and other.commitment_switches_today == 3


def test_persist_failure_is_logged_not_swallowed(caplog):
    # A broken control-state store must never crash a control decision (persistence is best-effort),
    # but it must NOT vanish silently — _persist logs a warning so a broken store is visible.
    def _boom(_snapshot):
        raise RuntimeError("disk full")

    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, on_state_change=_boom)
    with caplog.at_level(logging.WARNING):
        dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))
    assert dec.outcome == "applied"                 # the decision still succeeded (non-fatal)
    assert d.current_mode() is PhysicalMode.CHARGE
    assert "persist failed" in caplog.text
