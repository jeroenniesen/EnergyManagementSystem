"""I2 — refuse-when-busy self-restart: the safety core + the endpoint.

Two layers are proven here (both in one file, per the task brief):

1. The CONTROL-LAYER safety core (unit, no FastAPI): the unified outstanding-battery-work
   registry (reserve at submission, release only on real completion — never on a `wait_for`
   timeout), single-admission, and the no-yield `idle_for_restart()` read (registry-empty ∧
   not-unconfirmed ∧ confirmed-AUTO).
2. The WEB layer (TestClient): ADMIN+session gating, the supervised guard, single-flight,
   the 202 + response-attached SIGTERM trigger (spied), the audit row, `boot_id`,
   `restart_available`/`restart_pending`, and route registration only with an `auth_store`.

Plus a `_is_supervised()` truthy-parsing table and the `restart_pending` boot fingerprint.
"""
from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.control.service import ControlContext, ControlService
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.settings import effective_settings
from ems.sources.battery import BatteryWriteUnconfirmed, MockBatteryDriver
from ems.sources.mock import MockSource
from ems.storage.audit import AuditStore
from ems.storage.auth import AuthStore
from ems.storage.control_state import CONTROL_STATE_CORRUPT, ControlStateStore
from ems.storage.settings import SettingsStore
from ems.web.api import _is_supervised, _spawn_tracked, create_app


def _drain(loop: asyncio.AbstractEventLoop) -> None:
    """Let still-pending (shielded to_thread) worker tasks finish before `loop.close()`, so asyncio
    doesn't log 'Task was destroyed but it is pending' at teardown. The tests below poll registry
    state with blocking `time.sleep`, which never gives the loop a turn to observe a worker's
    to_thread future resolving — this drives one final drain."""
    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------------
# Control-layer harness (mirrors ems/tests/test_control_service.py:_service)
# --------------------------------------------------------------------------------------------
def _controlling_controller(driver=None) -> ModeController:
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(NOW)  # -> CONTROLLING
    return ModeController(driver or MockBatteryDriver(), lc, dry_run=False)


def _service(controller: ModeController, *, dry_run: bool = False):
    ctx = ControlContext()
    settings = effective_settings({})
    svc = ControlService(
        ctx=ctx, settings=settings, controller=controller, store=None, audit_store=None,
        price_source=None, solar_forecast=None,
        site_tz=ZoneInfo("Europe/Amsterdam"), dry_run=dry_run,
        current_soc=lambda now: 50.0,
        current_mode=lambda now: PhysicalMode.AUTO,
        current_towers=lambda now: None,
        data_quality=lambda now: "fresh",
        car_charging=lambda now: False,
        load_by=lambda starts: {s: 0.0 for s in starts},
        active_strategy=lambda now: "winter",
        validate_plan_obj=lambda plan, now: (_ for _ in ()).throw(AssertionError("unused")),
        planner_cfg=lambda: None,
        summer_cfg=lambda soc: None,
        adaptive_cfg=lambda: None,
    )
    return svc, ctx


class _UnconfirmedDriver(MockBatteryDriver):
    """apply() raises BatteryWriteUnconfirmed (device slow/unreachable) — the TIMED-OUT write."""

    def apply(self, mode, *, target_soc=None, power_w=None):
        raise BatteryWriteUnconfirmed("device slow")


class _RecordingDriver(MockBatteryDriver):
    """Records every apply() so a test can assert whether a battery write actually happened."""

    def __init__(self):
        super().__init__()
        self.applied: list = []

    def apply(self, mode, *, target_soc=None, power_w=None):
        self.applied.append(mode)
        return super().apply(mode, target_soc=target_soc, power_w=power_w)


# --------------------------------------------------------------------------------------------
# 1. Writer registry + idle_for_restart (the safety core)
# --------------------------------------------------------------------------------------------
def test_idle_for_restart_true_when_registry_empty_and_confirmed_auto():
    controller = _controlling_controller()  # starts AUTO, last_confirmed=None, not unconfirmed
    svc, _ctx = _service(controller)
    idle, reason = svc.idle_for_restart()
    assert idle is True, reason


def test_idle_for_restart_409_when_a_generic_writer_slot_is_reserved():
    # An overrun-AUTO worker "admitted-but-not-yet-writing" reserves a slot at submission.
    controller = _controlling_controller()
    svc, _ctx = _service(controller)
    token = svc.reserve_writer()
    assert svc.writer_registry_empty() is False
    idle, reason = svc.idle_for_restart()
    assert idle is False and reason == "outstanding_write"
    svc.release_writer(token)
    assert svc.writer_registry_empty() is True
    assert svc.idle_for_restart()[0] is True


def test_idle_for_restart_409_heterogeneous_multi_slot():
    # A control-tick slot AND an override/overrun slot outstanding together — both held.
    controller = _controlling_controller()
    svc, _ctx = _service(controller)
    cycle_token = svc._admit_cycle()
    generic_token = svc.reserve_writer()
    assert cycle_token is not None
    assert svc.idle_for_restart()[0] is False
    svc.release_writer(generic_token)
    assert svc.idle_for_restart()[0] is False  # cycle slot still held
    svc._release_cycle(cycle_token)
    assert svc.idle_for_restart()[0] is True


def test_single_admission_refuses_a_second_control_tick():
    controller = _controlling_controller()
    svc, _ctx = _service(controller)
    t1 = svc._admit_cycle()
    assert t1 is not None
    assert svc._admit_cycle() is None  # a second tick is refused while the first slot is held
    svc._release_cycle(t1)
    assert svc._admit_cycle() is not None  # freed → a fresh tick may be admitted


def test_admit_cycle_refused_while_draining():
    # P1 (Sol): draining is set by a restart that passed the gate DURING run_cycle's refresh_car_obs
    # await (before _admit_cycle). Admission must then refuse, or the resumed cycle would start a
    # writer that could outlive shutdown and leave the battery forced.
    controller = _controlling_controller()
    svc, _ctx = _service(controller)
    svc.mark_draining()
    assert svc._admit_cycle() is None  # no writer may start once draining
    svc.clear_draining()
    assert svc._admit_cycle() is not None


def test_admit_cycle_refused_while_any_writer_token_outstanding():
    # P1 (Sol, pass 2): an overrun-AUTO recovery token can outlive its timeout after the original
    # tick finished. cycle_in_flight would be false, but a new cycle must NOT start and race the
    # recovery worker — admission requires an EMPTY registry, not merely "no other cycle".
    controller = _controlling_controller()
    svc, _ctx = _service(controller)
    overrun_token = svc.reserve_writer()  # a lingering generic (overrun) writer
    assert svc._admit_cycle() is None  # refused: a writer is still outstanding
    svc.release_writer(overrun_token)
    assert svc._admit_cycle() is not None  # registry empty again → admit


def test_reserve_writer_refused_while_draining():
    # P1 (Sol, pass 2): on an over-budget (non-timeout) cycle the cycle slot is already released
    # while _handle_overrun awaits its audit write; a restart can set draining in that window. The
    # generic (overrun) reservation must then refuse atomically — no new writer during shutdown.
    controller = _controlling_controller()
    svc, _ctx = _service(controller)
    svc.mark_draining()
    assert svc.reserve_writer() is None
    svc.clear_draining()
    assert svc.reserve_writer() is not None


def test_idle_for_restart_409_when_last_command_unconfirmed_even_if_auto():
    # The naive predicate wrongly accepted this: a timed-out write leaves last_confirmed_action
    # AUTO/None but the device state is UNKNOWN — the persisted flag must block the restart.
    controller = _controlling_controller(_UnconfirmedDriver())
    svc, _ctx = _service(controller)
    controller.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, NOW, target_soc=80)
    assert controller.last_command_unconfirmed is True
    assert controller.last_confirmed_action in (None, PhysicalMode.AUTO)
    idle, reason = svc.idle_for_restart()
    assert idle is False and reason == "last_command_unconfirmed"


def test_idle_for_restart_409_when_last_action_not_auto():
    controller = _controlling_controller()
    svc, _ctx = _service(controller)
    controller.last_confirmed_action = PhysicalMode.CHARGE
    idle, reason = svc.idle_for_restart()
    assert idle is False and reason == "last_action_not_auto"


def test_override_reserves_registry_slot_at_submission_independent_of_override_tasks():
    # Fix 1 (spec §B.0.2): an override reserves its cycle slot in the UNIFIED registry SYNCHRONOUSLY
    # at submission — BEFORE its asyncio.Task is created — so the restart gate refuses via the
    # registry ALONE, with NO reliance on the override_tasks set. This is the window the old
    # separate override_tasks check used to cover; the registry now subsumes it.
    controller = _controlling_controller()
    svc, ctx = _service(controller)
    token = svc.reserve_override_cycle()  # exactly what the override endpoint calls at submission
    assert token is not None
    # The override worker has NOT run yet, and override_tasks is EMPTY — but the registry already
    # reflects the override the instant it is admitted, so the gate refuses.
    assert not ctx.override_tasks
    assert svc.writer_registry_empty() is False
    idle, reason = svc.idle_for_restart()
    assert idle is False and reason == "outstanding_write"  # visible via the registry alone
    # Released on the worker's real completion (here, directly) → the gate reopens.
    svc._release_cycle(token)
    assert svc.writer_registry_empty() is True
    assert svc.idle_for_restart()[0] is True


def test_override_run_cycle_reuses_presubmitted_slot_and_releases_on_real_completion(monkeypatch):
    # The pre-reserved override slot is USED by run_cycle (NOT a second admission) and released only
    # when the tick worker really completes. The restart gate stays 409 the whole time, via the
    # registry — override_tasks is never consulted.
    import ems.perf as perf
    monkeypatch.setitem(perf.PERF_BUDGETS, "control.cycle", 40)  # 40 ms deadline

    controller = _controlling_controller()
    svc, ctx = _service(controller)

    gate = threading.Event()

    def _blocking_tick(now):
        gate.wait(5.0)  # outlive the wait_for deadline
        return []

    async def _noop_overrun(*a, **k):
        return None

    monkeypatch.setattr(svc, "control_tick", _blocking_tick)
    monkeypatch.setattr(svc, "_handle_overrun", _noop_overrun)

    token = svc.reserve_override_cycle()  # SUBMISSION: slot reserved before any task/worker exists
    assert token is not None
    assert len(ctx.writer_registry) == 1
    assert svc.idle_for_restart() == (False, "outstanding_write")  # refused at submission already

    loop = asyncio.new_event_loop()
    try:
        # run_cycle uses the PRE-RESERVED token (does not admit a second slot) and returns after the
        # deadline while the blocked worker keeps holding the slot.
        loop.run_until_complete(svc.run_cycle(cycle_token=token))
        assert len(ctx.writer_registry) == 1  # still exactly ONE slot — token reused, not doubled
        assert svc.idle_for_restart()[0] is False  # worker still running → gate refuses
        gate.set()  # let the worker complete → its `finally` releases the slot on REAL completion
        deadline = time.time() + 3.0
        while not svc.writer_registry_empty() and time.time() < deadline:
            time.sleep(0.01)
        assert svc.writer_registry_empty() is True
        assert svc.idle_for_restart()[0] is True
    finally:
        gate.set()
        _drain(loop)  # drain the shielded worker → no "Task was destroyed but pending" warning
        loop.close()


def test_override_reservation_never_refuses_while_a_control_cycle_is_outstanding():
    # Fix 1 (SAFETY): single-admission (refuse-a-second-concurrent) is for the PERIODIC TICK ONLY.
    # An override submitted while an ordinary control cycle is outstanding must STILL reserve its
    # own slot (reserve-but-never-refuse) — it may never be dropped/coalesced, which previously
    # delayed clear/return-to-AUTO and car-guard priority by up to a full control cadence.
    controller = _controlling_controller()
    svc, ctx = _service(controller)
    cycle_token = svc._admit_cycle()  # a periodic tick is outstanding
    assert cycle_token is not None
    assert svc._admit_cycle() is None  # a SECOND periodic tick is (correctly) refused
    override_token = svc.reserve_override_cycle()  # but an OVERRIDE is not
    assert override_token is not None
    assert len(ctx.writer_registry) == 2  # both slots held → restart 409s until both complete
    assert svc.idle_for_restart() == (False, "outstanding_write")
    svc._release_cycle(cycle_token)
    svc._release_cycle(override_token)
    # The ONLY thing that refuses an override is draining (a restart is imminent; the persisted
    # override is re-read on boot) — never a mere outstanding cycle.
    svc.mark_draining()
    assert svc.reserve_override_cycle() is None
    svc.clear_draining()
    assert svc.reserve_override_cycle() is not None


def test_override_applies_promptly_after_current_writer_not_next_periodic_tick(monkeypatch):
    # Fix 1 (SAFETY regression): an override submitted WHILE an ordinary control cycle holds the
    # writer must apply as soon as that writer completes — queued behind it via control_lock — NOT
    # be deferred to the next periodic tick (~control_cycle_seconds later). This is what makes
    # clear/return-to-AUTO and car-guard priority act at once.
    controller = _controlling_controller()
    svc, ctx = _service(controller)

    tick_calls: list = []

    def _record_tick(now):
        tick_calls.append(now)
        return []

    monkeypatch.setattr(svc, "control_tick", _record_tick)

    async def scenario():
        # Simulate the CURRENT writer (a periodic cycle) holding the control lock.
        await ctx.control_lock.acquire()
        tasks: set = set()
        override_task = svc.spawn_override_cycle(
            lambda coro: _spawn_tracked(coro, "Override control cycle", tasks))
        assert override_task is not None
        assert len(ctx.writer_registry) == 1  # slot reserved AT SUBMISSION (before spawn)
        assert svc.idle_for_restart() == (False, "outstanding_write")
        await asyncio.sleep(0.02)  # give the override a turn — it must STILL be blocked on the lock
        assert tick_calls == []  # NOT applied: queued behind the current writer, not on a tick
        assert svc.idle_for_restart() == (False, "outstanding_write")  # still held while queued
        ctx.control_lock.release()  # the current writer completes → releases the lock
        await asyncio.wait_for(override_task, 3.0)  # the override now runs PROMPTLY
        assert len(tick_calls) == 1  # applied right after the writer — not deferred to the tick
        assert svc.writer_registry_empty() is True  # slot released on the worker's real completion
        assert svc.idle_for_restart()[0] is True

    asyncio.run(scenario())


def test_override_pre_start_cancel_releases_slot_no_leak():
    # Fix 2 (leak → permanent-busy deadlock): the override slot is reserved AT SUBMISSION, OUTSIDE
    # the run_cycle coroutine. If that task is cancelled before its first turn, run_cycle never
    # reaches its slot-releasing finally — the token leaks FOREVER and wedges idle_for_restart at
    # "outstanding_write" (a restart could never succeed again). Sol reproduced registry_size==1
    # after a pre-start cancel; the done-callback releases the orphaned slot so the gate recovers.
    controller = _controlling_controller()
    svc, ctx = _service(controller)

    async def scenario():
        tasks: set = set()
        task = svc.spawn_override_cycle(
            lambda coro: _spawn_tracked(coro, "Override control cycle", tasks))
        assert task is not None
        assert len(ctx.writer_registry) == 1  # reserved at submission
        assert svc.idle_for_restart() == (False, "outstanding_write")
        task.cancel()  # cancel BEFORE the loop steps it → run_cycle never starts
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)  # let the done-callback (_release_if_orphaned) run
        assert svc.writer_registry_empty() is True  # orphaned slot released → no leak
        assert svc.idle_for_restart()[0] is True  # gate recovers

    asyncio.run(scenario())


def test_override_synchronous_spawn_failure_releases_slot_no_leak():
    # Fix 2 guard (a): if task creation itself raises, run_cycle never runs — the pre-reserved slot
    # must be released synchronously so it can't leak.
    controller = _controlling_controller()
    svc, _ctx = _service(controller)

    def _boom_spawn(coro):
        raise RuntimeError("cannot create task")

    with pytest.raises(RuntimeError):
        svc.spawn_override_cycle(_boom_spawn)
    assert svc.writer_registry_empty() is True  # slot released despite the spawn failure


def test_idle_for_restart_clears_unconfirmed_flag_on_confirmed_auto():
    # A confirmed AUTO recovery (existing model) clears the flag → idle again.
    controller = _controlling_controller(_UnconfirmedDriver())
    svc, _ctx = _service(controller)
    controller.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, NOW, target_soc=80)
    assert svc.idle_for_restart()[0] is False
    # Swap in a healthy driver whose device is NOT already AUTO, so the AUTO recovery is a REAL
    # confirmed write (an idempotent no-op deliberately does NOT clear the flag — conservative).
    controller.driver = MockBatteryDriver()
    controller.driver.apply(PhysicalMode.CHARGE)  # device in CHARGE → the AUTO decide really writes
    controller.decide(BatteryIntent.ALLOW_SELF_CONSUMPTION, NOW + timedelta(hours=2))
    assert controller.last_command_unconfirmed is False
    assert controller.last_confirmed_action is PhysicalMode.AUTO
    assert svc.idle_for_restart()[0] is True


def test_confirmed_auto_reconcile_unwedges_the_gate():
    # P2 (Sol, pass 2): a prior process's shutdown-restore confirmed AUTO on the device but left the
    # persisted last_confirmed_action non-AUTO; the new process restores that stale value and, when
    # plan wants AUTO (idempotent cycles), the gate would sit at 409 forever. note_confirmed_auto()
    # (called by _shutdown_restore on a confirmed AUTO) reconciles it so a restart is available.
    controller = _controlling_controller()
    svc, _ctx = _service(controller)
    controller.last_confirmed_action = PhysicalMode.CHARGE  # stale, restored from a prior process
    assert svc.idle_for_restart()[0] is False
    controller.note_confirmed_auto()  # what _shutdown_restore now does on a confirmed AUTO
    assert controller.last_confirmed_action is PhysicalMode.AUTO
    assert svc.idle_for_restart()[0] is True


def test_dry_run_service_is_always_safe():
    controller = _controlling_controller()
    controller.last_confirmed_action = PhysicalMode.CHARGE  # would block if armed
    svc, _ctx = _service(controller, dry_run=True)
    assert svc.idle_for_restart()[0] is True


def test_idle_for_restart_409_while_draining():
    # Fix 2 (defense-in-depth): idle_for_restart consults `draining` directly, so a stuck/leftover
    # drain can NEVER read as idle and let a fresh restart proceed on top of it. The implicit
    # invariant (draining ⇒ _restart_requested) is no longer the only thing standing in the way.
    controller = _controlling_controller()
    svc, _ctx = _service(controller)
    assert svc.idle_for_restart()[0] is True  # clean baseline
    svc.mark_draining()
    idle, reason = svc.idle_for_restart()
    assert idle is False and reason == "draining"
    svc.clear_draining()
    assert svc.idle_for_restart()[0] is True


def test_restore_corrupt_blob_fails_safe_and_idle_refuses():
    # Fix 1 (3-reviewer consensus, FAIL-SAFE): a corrupt/unparseable persisted blob must leave
    # last_command_unconfirmed=True (unknown device state ⇒ block the restart) — NOT the old
    # fail-OPEN False. Even an explicit `False` in the blob is overridden when another field is
    # garbage, because the parse never reaches it and the except fails safe.
    controller = _controlling_controller()
    controller.restore_state(
        {"switches_today": "not-an-int", "last_command_unconfirmed": False})
    assert controller.last_command_unconfirmed is True
    svc, _ctx = _service(controller)
    idle, reason = svc.idle_for_restart()
    assert idle is False and reason == "last_command_unconfirmed"


def test_restore_missing_unconfirmed_key_fails_safe():
    # Fix 1: a present blob that LACKS the key (e.g. a pre-I2 persisted state) can't prove the last
    # command confirmed → fail safe to True; a well-formed explicit False still reads False.
    missing = _controlling_controller()
    missing.restore_state({"switches_today": 2})  # no last_command_unconfirmed key
    assert missing.last_command_unconfirmed is True
    present = _controlling_controller()
    present.restore_state({"switches_today": 2, "last_command_unconfirmed": False})
    assert present.last_command_unconfirmed is False


def test_corrupt_persisted_row_fails_safe_through_the_real_store(tmp_path):
    # INTEGRATION (the missing coverage): exercise the REAL store→restore boundary, NOT a direct
    # malformed-field call. load() used to collapse a corrupt row to a fresh {} — so restore_state
    # left last_command_unconfirmed=False and the gate opened (fail OPEN with an UNKNOWN device
    # state). Now a corrupt row surfaces as CONTROL_STATE_CORRUPT, restore_state fails SAFE, and the
    # refuse-when-busy gate refuses with the unconfirmed reason.
    db = str(tmp_path / "ems.sqlite")
    store = ControlStateStore(db)
    store.init()
    con = sqlite3.connect(db)  # write invalid JSON straight into the backing row
    con.execute("INSERT INTO control_state (key, value) VALUES (?, ?)", ("controller", "{corrupt"))
    con.commit()
    con.close()

    loaded = store.load()
    assert loaded is CONTROL_STATE_CORRUPT  # the load boundary now DISTINGUISHES corrupt from {}
    controller = _controlling_controller()
    controller.restore_state(loaded)
    assert controller.last_command_unconfirmed is True
    svc, _ctx = _service(controller)
    assert svc.idle_for_restart() == (False, "last_command_unconfirmed")


def test_absent_persisted_store_is_fresh_and_restart_not_regressed(tmp_path):
    # The COMPANION non-regression: a genuinely-absent store (table exists, NO row) stays fresh →
    # last_command_unconfirmed=False, so a fresh-boot restart is STILL permitted (an armed
    # controlling service reads idle). Proves the fix did not turn "nothing persisted" into a block.
    db = str(tmp_path / "ems.sqlite")
    store = ControlStateStore(db)
    store.init()  # table exists, no row → genuinely fresh
    loaded = store.load()
    assert loaded == {}

    armed = _controlling_controller()  # dry_run=False, armed
    armed.restore_state(loaded)
    assert armed.last_command_unconfirmed is False  # fresh, NOT fail-safe
    assert _service(armed)[0].idle_for_restart()[0] is True  # armed fresh boot may restart

    # And the dry-run short-circuit is untouched: a dry-run service is always safe regardless.
    dry = _controlling_controller()
    dry.restore_state(loaded)
    assert _service(dry, dry_run=True)[0].idle_for_restart()[0] is True


# --------------------------------------------------------------------------------------------
# Slot lifecycle through run_cycle: reserved at submission, released ONLY on real completion
# --------------------------------------------------------------------------------------------
def test_slot_reserved_at_submission_and_released_only_on_real_completion(monkeypatch):
    import ems.perf as perf
    monkeypatch.setitem(perf.PERF_BUDGETS, "control.cycle", 40)  # 40 ms deadline

    controller = _controlling_controller()
    svc, _ctx = _service(controller)

    gate = threading.Event()

    def _blocking_tick(now):
        gate.wait(5.0)  # outlive the wait_for deadline
        return []

    async def _noop_overrun(*a, **k):
        return None

    monkeypatch.setattr(svc, "control_tick", _blocking_tick)
    monkeypatch.setattr(svc, "_handle_overrun", _noop_overrun)

    loop = asyncio.new_event_loop()
    try:
        # run_cycle returns after the wait_for deadline (~40 ms); the tick worker runs on.
        loop.run_until_complete(svc.run_cycle())
        # The wait_for timed out — but the slot must STILL be held (worker not finished).
        assert svc.writer_registry_empty() is False
        assert svc.idle_for_restart()[0] is False
        # Release the worker; the blocking function's `finally` frees the slot on REAL completion.
        gate.set()
        deadline = time.time() + 3.0
        while not svc.writer_registry_empty() and time.time() < deadline:
            time.sleep(0.01)
        assert svc.writer_registry_empty() is True
    finally:
        gate.set()
        _drain(loop)  # drain the shielded worker → no "Task was destroyed but pending" warning
        loop.close()


def test_queued_writer_survives_timeout_cancellation_and_releases_slot(monkeypatch):
    # P1 (Sol): under executor saturation a QUEUED worker cancelled by wait_for would never run its
    # slot-releasing `finally` → the slot leaks forever and coalesces every future cycle. The shield
    # must keep the task alive so the thread eventually runs and releases. Reproduce with a
    # single-thread executor occupied by another job so the tick worker queues behind it.
    from concurrent.futures import ThreadPoolExecutor

    import ems.perf as perf
    monkeypatch.setitem(perf.PERF_BUDGETS, "control.cycle", 40)  # 40 ms deadline

    controller = _controlling_controller()
    svc, _ctx = _service(controller)

    async def _noop_overrun(*a, **k):
        return None

    started = threading.Event()

    def _tick(now):
        started.set()  # only reached once the executor frees up (proves it wasn't dropped)
        return []

    monkeypatch.setattr(svc, "_handle_overrun", _noop_overrun)
    monkeypatch.setattr(svc, "control_tick", _tick)

    occupy_release = threading.Event()
    loop = asyncio.new_event_loop()
    ex = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(ex)
    try:
        ex.submit(lambda: occupy_release.wait(5.0))  # occupy the one thread → tick worker QUEUES
        loop.run_until_complete(svc.run_cycle())  # times out while the worker is still queued
        assert started.is_set() is False  # worker never started (queued)
        assert svc.writer_registry_empty() is False  # slot reserved at submission, still held
        occupy_release.set()  # free the executor → the shielded, queued worker finally runs
        deadline = time.time() + 3.0
        while not svc.writer_registry_empty() and time.time() < deadline:
            time.sleep(0.01)
        assert started.is_set() is True  # it DID run — cancellation didn't drop it from the queue
        assert svc.writer_registry_empty() is True  # and released its slot on real completion
    finally:
        occupy_release.set()
        ex.shutdown(wait=True)
        _drain(loop)  # drain the shielded worker → no "Task was destroyed but pending" warning
        loop.close()


def test_overrun_auto_recovery_reserves_at_submission_and_releases_on_completion(monkeypatch):
    # The overrun-AUTO recovery is the 3rd write path: it must reserve a slot at submission and
    # force the battery to AUTO off-thread, releasing only on the worker's real completion.
    import ems.perf as perf
    monkeypatch.setitem(perf.PERF_BUDGETS, "control.cycle", 20_000)
    controller = _controlling_controller()
    controller.driver.apply(PhysicalMode.CHARGE)  # non-AUTO so the recovery is a real write
    svc, _ctx = _service(controller)

    class _Sample:
        duration_ms = 99_999.0

    asyncio.run(svc._handle_overrun(datetime.now(UTC), True, _Sample()))
    assert controller.driver.current_mode() is PhysicalMode.AUTO  # forced to AUTO off the loop
    assert svc.writer_registry_empty() is True  # slot released on real completion
    # P1 (Sol, pass 2): the overrun path must NEVER open the gate — a racing timed-out tick could
    # apply a different mode after this AUTO. It marks the last command unconfirmed unconditionally,
    # so a restart is blocked until a normal confirmed cycle re-establishes safe AUTO.
    assert controller.last_command_unconfirmed is True
    assert svc.idle_for_restart()[0] is False


def test_overrun_unconfirmed_recovery_sets_the_unconfirmed_flag():
    # P1 (Sol): if the forced-AUTO recovery itself is unconfirmed, the newest battery command's
    # device state is unknown — the gate MUST block a subsequent restart (the overrun path writes
    # AUTO straight through the driver, so it has to record its own outcome).
    import ems.perf as perf

    controller = _controlling_controller(_UnconfirmedDriver())
    svc, _ctx = _service(controller)
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(perf.PERF_BUDGETS, "control.cycle", 20_000)

        class _Sample:
            duration_ms = 99_999.0

        asyncio.run(svc._handle_overrun(datetime.now(UTC), True, _Sample()))
    assert controller.last_command_unconfirmed is True
    assert svc.writer_registry_empty() is True  # slot still released on real completion
    idle, reason = svc.idle_for_restart()
    assert idle is False and reason == "last_command_unconfirmed"


def test_handle_overrun_synchronous_spawn_failure_releases_slot_no_leak(monkeypatch):
    # Fix 3 (leak → permanent-busy deadlock): the overrun-AUTO slot is reserved BEFORE the worker is
    # spawned. If _spawn_writer/ensure_future raises SYNCHRONOUSLY (before the to_thread task exists
    # to own the release), the worker's slot-releasing `finally` never runs — without a leak guard
    # the token would leak forever and `_admit_cycle` would then refuse EVERY future tick. The
    # `not spawned` finally (mirroring run_cycle) must release the pre-reserved token.
    import ems.perf as perf

    controller = _controlling_controller()
    controller.driver.apply(PhysicalMode.CHARGE)  # non-AUTO so the recovery would be a real write
    svc, _ctx = _service(controller)

    def _boom_spawn(coro):
        coro.close()  # suppress "coroutine was never awaited" — the test's mock, not the SUT
        raise RuntimeError("cannot create task")

    monkeypatch.setattr(svc, "_spawn_writer", _boom_spawn)
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(perf.PERF_BUDGETS, "control.cycle", 20_000)

        class _Sample:
            duration_ms = 99_999.0

        # The synchronous spawn failure is swallowed (non-fatal) by _handle_overrun's except.
        asyncio.run(svc._handle_overrun(datetime.now(UTC), True, _Sample()))
    assert svc.writer_registry_empty() is True  # pre-reserved slot released → no leak
    assert svc._admit_cycle() is not None  # and a future tick can still be admitted


def test_handle_overrun_while_draining_starts_no_battery_write():
    # Fix 7 (shutdown-window safety): once `draining` is set for a restart, an over-budget cycle's
    # forced-AUTO recovery must NOT start a new battery write — reserve_writer refuses while
    # draining, so no apply() reaches the device in the exit window (the lifespan _shutdown_restore
    # sets AUTO on the way out anyway).
    import ems.perf as perf

    driver = _RecordingDriver()
    controller = _controlling_controller(driver)
    driver.applied.clear()  # ignore the probe/setup path — only care about the overrun write
    svc, _ctx = _service(controller)
    svc.mark_draining()
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(perf.PERF_BUDGETS, "control.cycle", 20_000)

        class _Sample:
            duration_ms = 99_999.0

        asyncio.run(svc._handle_overrun(datetime.now(UTC), True, _Sample()))
    assert driver.applied == []  # no new battery write started during the shutdown window
    assert svc.writer_registry_empty() is True  # and nothing was left reserved in the registry


# --------------------------------------------------------------------------------------------
# 2. _is_supervised() strict truthy allow-list
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "yes", "on", "ON", "  on  "])
def test_is_supervised_truthy(monkeypatch, value):
    monkeypatch.setenv("EMS_SUPERVISED", value)
    assert _is_supervised() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "", "maybe", "2", "no"])
def test_is_supervised_falsy(monkeypatch, value):
    monkeypatch.setenv("EMS_SUPERVISED", value)
    assert _is_supervised() is False


def test_is_supervised_unset(monkeypatch):
    monkeypatch.delenv("EMS_SUPERVISED", raising=False)
    assert _is_supervised() is False


# --------------------------------------------------------------------------------------------
# 3. Web layer — gating, supervised guard, single-flight, 202, audit, boot_id
# --------------------------------------------------------------------------------------------
def _seed_user(db: str, username: str, password: str, role: str):
    from ems.authn import hash_password
    s = AuthStore(db)

    async def run():
        await s.init()
        await s.create_user(username, hash_password(password), role)
        await s.close()

    asyncio.run(run())


def _full_app(db: str):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=AuthStore(db),
        audit_store=AuditStore(db),
    )


def _login(c: TestClient, username: str, password: str) -> dict:
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


class _Spy:
    def __init__(self, *, raises: bool = False):
        self.calls = 0
        self._raises = raises

    def __call__(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("trigger blew up")


def _read_auth_audit(db: str) -> list[dict]:
    """The recent category='auth' audit rows (where the restart lifecycle events are written)."""
    async def run():
        s = AuditStore(db)
        rows = await s.recent(limit=50, category="auth")
        await s.close()
        return rows

    return asyncio.run(run())


def test_route_absent_without_auth_store():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock")  # auth_store=None
    assert "/api/system/restart" not in {getattr(r, "path", None) for r in app.routes}
    with TestClient(app) as c:
        assert c.post("/api/system/restart", json={}).status_code == 404


def test_health_live_exposes_boot_id():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock")
    with TestClient(app) as c:
        body = c.get("/health/live").json()
        assert body["status"] == "alive"
        assert body["boot_id"] == app.state.boot_id
        assert isinstance(body["boot_id"], str) and body["boot_id"]


def test_403_for_non_admin_session(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "op", "pw12345678", "user")
    app = _full_app(db)
    with TestClient(app) as c:
        h = _login(c, "op", "pw12345678")
        assert c.post("/api/system/restart", json={}, headers=h).status_code == 403


def test_403_for_admin_access_token_session_only(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")  # session
        minted = c.post("/api/auth/tokens", json={"name": "cli", "tier": "admin"}, headers=h)
        assert minted.status_code == 200, minted.text
        access = {"Authorization": f"Bearer {minted.json()['token']}"}
        # ADMIN-tier is satisfied by the access token, but the path is session-only → 403.
        assert c.post("/api/system/restart", json={}, headers=access).status_code == 403


def test_409_unsupervised(tmp_path, monkeypatch):
    monkeypatch.delenv("EMS_SUPERVISED", raising=False)
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    app.state.request_restart = _Spy()  # never reached, but be safe
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        r = c.post("/api/system/restart", json={}, headers=h)
        assert r.status_code == 409
        assert "not supervised" in r.json()["detail"]


def test_202_success_invokes_trigger_and_audits(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    spy = _Spy()
    app.state.request_restart = spy
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        r = c.post("/api/system/restart", json={}, headers=h)
        assert r.status_code == 202, r.text
        assert r.json() == {"restarting": True, "boot_id": app.state.boot_id}
        assert spy.calls == 1  # response-attached trigger fired

        async def _read_audit():
            s = AuditStore(db)
            rows = await s.recent(limit=50, category="auth")
            await s.close()
            return rows

        rows = asyncio.run(_read_audit())
        assert any(row.get("detail", {}).get("event") == "system_restart" for row in rows)


def test_409_second_concurrent_request_single_flight(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    app.state.request_restart = _Spy()  # does NOT clear the single-flight flag
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        assert c.post("/api/system/restart", json={}, headers=h).status_code == 202
        second = c.post("/api/system/restart", json={}, headers=h)
        assert second.status_code == 409  # already in progress


def test_409_busy_when_a_writer_slot_is_reserved(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    app.state.request_restart = _Spy()
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        token = app.state.control_service.reserve_writer()  # simulate an outstanding write
        try:
            r = c.post("/api/system/restart", json={}, headers=h)
            assert r.status_code == 409
            assert r.json()["reason"] == "outstanding_write"
        finally:
            app.state.control_service.release_writer(token)


def test_failing_trigger_clears_flags_no_wedge(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    app.state.request_restart = _Spy(raises=True)
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        # 202 is still returned (body sent before the trigger runs); the trigger then raises and
        # BOTH wedge flags are cleared so a retry is NOT wedged at 409.
        assert c.post("/api/system/restart", json={}, headers=h).status_code == 202
        assert app.state._restart_requested is False
        assert app.state.control_service._ctx.draining is False  # draining cleared too (no wedge)
        # The trigger failure is audited so an operator can see it blew up.
        rows = _read_auth_audit(db)
        assert any(r.get("detail", {}).get("event") == "system_restart_failed" for r in rows)
        app.state.request_restart = _Spy()  # a working trigger this time
        assert c.post("/api/system/restart", json={}, headers=h).status_code == 202


def test_watchdog_clears_flags_when_trigger_never_runs_no_wedge(tmp_path, monkeypatch):
    # P1 (the WEDGE): the SIGTERM trigger is a response-attached BackgroundTask that Starlette runs
    # ONLY on a successful send. If the request is cancelled after mark_draining() but before that
    # task fires, the trigger NEVER runs — so nothing clears `_restart_requested`/`draining` and the
    # controller is wedged FOREVER (cycles suppressed, every retry 409). Simulate the trigger never
    # firing by dropping it (an async no-op that does NOT set `_restart_committed`, exactly the
    # state a never-run trigger leaves). The independent watchdog must un-wedge both flags.
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    app.state._restart_watchdog_seconds = 0.05  # keep the test fast

    async def _dropped_trigger():
        return None  # the response-attached task effectively never ran (no commit, no SIGTERM)

    real_trigger = app.state._restart_trigger  # the production trigger, to restore after the abort
    app.state._restart_trigger = _dropped_trigger
    ctx = app.state.control_service._ctx
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        assert c.post("/api/system/restart", json={}, headers=h).status_code == 202
        # The watchdog is armed but hasn't fired yet: both wedge flags are set (single-flight latch
        # + draining, which suppresses control cycles), and a retry is (transiently) 409.
        assert app.state._restart_requested is True
        assert ctx.draining is True
        assert c.post("/api/system/restart", json={}, headers=h).status_code == 409

        # Wait for the watchdog to un-wedge (bounded — do NOT hang).
        deadline = time.time() + 3.0
        while app.state._restart_requested and time.time() < deadline:
            time.sleep(0.02)
        assert app.state._restart_requested is False  # watchdog cleared the single-flight latch
        assert ctx.draining is False  # and cleared draining → control cycles resume
        # The abort is audited so the un-wedge is visible to an operator.
        rows = _read_auth_audit(db)
        assert any(r.get("detail", {}).get("event") == "system_restart_aborted" for r in rows)

        # And a subsequent restart is NOT permanently 409 — the controller recovered. Restore the
        # real trigger (with a spy for the actual SIGTERM) and prove a fresh request is accepted.
        app.state._restart_trigger = real_trigger
        app.state.request_restart = _Spy()
        assert c.post("/api/system/restart", json={}, headers=h).status_code == 202


# --------------------------------------------------------------------------------------------
# 4. restart_available / restart_pending surfaced on /api/auth/me + boot fingerprint
# --------------------------------------------------------------------------------------------
def test_me_restart_available_admin_session_supervised(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        me = c.get("/api/auth/me", headers=h).json()
        assert me["restart_available"] == {"available": True, "reason": "ok"}
        assert me["restart_pending"] is False


def test_me_restart_unavailable_when_unsupervised(tmp_path, monkeypatch):
    monkeypatch.delenv("EMS_SUPERVISED", raising=False)
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        me = c.get("/api/auth/me", headers=h).json()
        assert me["restart_available"]["available"] is False
        assert me["restart_available"]["reason"] == "not_supervised"


def test_me_restart_unavailable_for_reader(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "rdr", "pw12345678", "reader")
    app = _full_app(db)
    with TestClient(app) as c:
        h = _login(c, "rdr", "pw12345678")
        me = c.get("/api/auth/me", headers=h).json()
        assert me["restart_available"] == {"available": False, "reason": "not_admin"}


def test_restart_pending_tracks_restart_tagged_setting_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        assert c.get("/api/auth/me", headers=h).json()["restart_pending"] is False
        # Flip a restart-tagged setting to a NEW value → the post-load fingerprint diverges.
        current = c.get("/api/settings", headers=h).json()["values"]["connection.use_live_prices"]
        saved = c.post("/api/settings", json={"connection.use_live_prices": not current}, headers=h)
        assert saved.json()["restart_required"] is True
        assert c.get("/api/auth/me", headers=h).json()["restart_pending"] is True


def test_restart_pending_ignores_live_tagged_setting_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("EMS_SUPERVISED", "1")
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    app = _full_app(db)
    with TestClient(app) as c:
        h = _login(c, "admin", "pw12345678")
        saved = c.post("/api/settings", json={"ui.theme": "dark"}, headers=h)  # live-tagged
        assert saved.json()["restart_required"] is False
        assert c.get("/api/auth/me", headers=h).json()["restart_pending"] is False


# --------------------------------------------------------------------------------------------
# 5. authz classification
# --------------------------------------------------------------------------------------------
def test_restart_path_is_admin_and_session_only():
    from ems.web.authz import ADMIN_PATHS, Tier, required_tier, requires_session
    assert "/api/system/restart" in ADMIN_PATHS
    assert required_tier("/api/system/restart", "POST") == Tier.ADMIN
    assert requires_session("/api/system/restart") is True


def test_restart_endpoint_is_covered_by_write_gating_invariant(tmp_path):
    # The write-gating invariant (test_routes_wiring) builds the legacy no-auth app where the
    # restart route is absent. Prove that WITH an auth_store the route both EXISTS and is tiered
    # above VIEW (never a plain reader), so the endpoint is explicitly covered.
    from ems.web.authz import Tier, required_tier
    db = str(tmp_path / "ems.sqlite")
    app = _full_app(db)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/system/restart" in paths
    assert required_tier("/api/system/restart", "POST") != Tier.VIEW
