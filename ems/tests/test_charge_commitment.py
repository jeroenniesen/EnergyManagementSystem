"""#177 — restart-/deploy-safe GRID_CHARGE commitment.

Persists an active charge contract across shutdown_restore → AUTO (#127), then either resumes
CHARGE on a live boot or aborts with an explicit audited reason (never a silent wipe via
AUTO + dry_run). No hardware — mock drivers only.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.control.charge_commitment import (
    ABORT_DRY_RUN,
    ABORT_EXPIRED,
    ABORT_TARGET_REACHED,
    ABORT_UNARMED,
    ABORT_UNSAFE,
    ChargeCommitment,
    evaluate_commitment,
    shutdown_preserve_reason,
)
from ems.control.mode_controller import ModeController
from ems.control.service import ControlContext, ControlService
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle, OwnershipState
from ems.settings import effective_settings
from ems.storage.control_state import ControlStateStore

AMS = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(hours=2)


class _WritesDriver:
    def __init__(self, mode=PhysicalMode.AUTO, *, armed: bool = True):
        self._mode = mode
        self.writes: list[PhysicalMode] = []
        self._armed = armed

    @property
    def armed(self) -> bool:
        return self._armed

    def current_mode(self):
        return self._mode

    def configure_power_limits(self, *, max_charge_w: float, max_discharge_w: float) -> None:
        return None

    def apply(self, mode, *, target_soc=None, power_w=None):
        self.writes.append(mode)
        self._mode = mode
        return True

    def probe(self):
        raise RuntimeError("no probe")


def _commitment(**kwargs) -> ChargeCommitment:
    base = dict(
        target_soc=80.0,
        deadline=DEADLINE,
        reason="cheap window €0.30",
        applied_at=NOW,
        power_w=4800.0,
    )
    base.update(kwargs)
    return ChargeCommitment(**base)


# --- pure evaluate -------------------------------------------------------------------------------

def test_evaluate_none_when_absent():
    v = evaluate_commitment(
        None, now=NOW, soc_pct=20.0, dry_run=False, armed=True,
        data_quality="fresh", grace_elapsed=True)
    assert v.action == "none"


def test_evaluate_abort_expired():
    v = evaluate_commitment(
        _commitment(deadline=NOW - timedelta(minutes=1)),
        now=NOW, soc_pct=20.0, dry_run=False, armed=True,
        data_quality="fresh", grace_elapsed=True)
    assert v.action == "abort"
    assert ABORT_EXPIRED in v.reason


def test_evaluate_abort_target_reached():
    v = evaluate_commitment(
        _commitment(target_soc=50.0),
        now=NOW, soc_pct=55.0, dry_run=False, armed=True,
        data_quality="fresh", grace_elapsed=True)
    assert v.action == "abort"
    assert ABORT_TARGET_REACHED in v.reason


def test_evaluate_abort_dry_run():
    v = evaluate_commitment(
        _commitment(),
        now=NOW, soc_pct=20.0, dry_run=True, armed=True,
        data_quality="fresh", grace_elapsed=True)
    assert v.action == "abort"
    assert ABORT_DRY_RUN in v.reason
    assert "80%" in v.reason


def test_evaluate_abort_unarmed():
    v = evaluate_commitment(
        _commitment(),
        now=NOW, soc_pct=20.0, dry_run=False, armed=False,
        data_quality="fresh", grace_elapsed=True)
    assert v.action == "abort"
    assert ABORT_UNARMED in v.reason


def test_evaluate_abort_unsafe():
    v = evaluate_commitment(
        _commitment(),
        now=NOW, soc_pct=20.0, dry_run=False, armed=True,
        data_quality="unsafe", grace_elapsed=True)
    assert v.action == "abort"
    assert ABORT_UNSAFE in v.reason


def test_evaluate_hold_during_grace():
    v = evaluate_commitment(
        _commitment(),
        now=NOW, soc_pct=20.0, dry_run=False, armed=True,
        data_quality="fresh", grace_elapsed=False)
    assert v.action == "hold"


def test_evaluate_resume_when_valid():
    v = evaluate_commitment(
        _commitment(),
        now=NOW, soc_pct=20.0, dry_run=False, armed=True,
        data_quality="fresh", grace_elapsed=True)
    assert v.action == "resume"
    assert "resuming GRID_CHARGE" in v.reason


def test_shutdown_preserve_reason_mentions_auto_and_target():
    note = shutdown_preserve_reason(_commitment(target_soc=72.0))
    assert note is not None
    assert "AUTO" in note
    assert "72%" in note
    assert shutdown_preserve_reason(None) is None


def test_commitment_roundtrip_dict():
    c = _commitment()
    restored = ChargeCommitment.from_dict(c.to_dict())
    assert restored == c
    assert ChargeCommitment.from_dict({"intent": "hold_reserve", "target_soc": 50}) is None
    assert ChargeCommitment.from_dict(None) is None
    assert ChargeCommitment.from_dict({"target_soc": 50}) is None  # missing deadline


# --- control-state persistence -------------------------------------------------------------------

def test_commitment_survives_control_state_roundtrip(tmp_path):
    store = ControlStateStore(str(tmp_path / "c.sqlite"))
    store.init()
    ctl = ModeController(
        _WritesDriver(PhysicalMode.CHARGE), Lifecycle(dry_run=False), dry_run=False,
        on_state_change=store.save,
    )
    ctl.set_charge_commitment(_commitment())
    loaded = store.load()
    assert "charge_commitment" in loaded
    other = ModeController(
        _WritesDriver(PhysicalMode.AUTO), Lifecycle(dry_run=False), dry_run=False)
    other.restore_state(loaded)
    assert other.charge_commitment is not None
    assert other.charge_commitment.target_soc == 80.0
    assert other.charge_commitment.deadline == DEADLINE


def test_note_confirmed_auto_keeps_commitment(tmp_path):
    """#127 shutdown reconcile must NOT wipe the #177 commitment."""
    store = ControlStateStore(str(tmp_path / "c.sqlite"))
    store.init()
    ctl = ModeController(
        _WritesDriver(PhysicalMode.CHARGE), Lifecycle(dry_run=False), dry_run=False,
        on_state_change=store.save,
    )
    ctl.last_confirmed_action = PhysicalMode.CHARGE
    ctl.set_charge_commitment(_commitment())
    ctl.note_confirmed_auto()  # what _shutdown_restore does
    assert ctl.last_confirmed_action is PhysicalMode.AUTO
    assert ctl.charge_commitment is not None
    assert ctl.charge_commitment.target_soc == 80.0
    # Survives a fresh process restore too.
    boot = ModeController(
        _WritesDriver(PhysicalMode.AUTO), Lifecycle(dry_run=False), dry_run=False)
    boot.restore_state(store.load())
    assert boot.charge_commitment is not None
    assert boot.last_confirmed_action is PhysicalMode.AUTO


# --- control-tick resume / abort -----------------------------------------------------------------

def _service(controller, *, dry_run: bool, soc: float = 20.0, data_quality: str = "fresh"):
    ctx = ControlContext()
    svc = ControlService(
        ctx=ctx,
        settings=effective_settings({}),
        controller=controller,
        store=None,
        audit_store=None,
        price_source=None,
        solar_forecast=None,
        site_tz=AMS,
        dry_run=dry_run,
        current_soc=lambda now: soc,
        current_mode=lambda now: controller.driver.current_mode(),
        current_towers=lambda now: None,
        data_quality=lambda now: data_quality,
        car_charging=lambda now: False,
        load_by=lambda starts: {s: 0.0 for s in starts},
        active_strategy=lambda now: "winter",
        validate_plan_obj=lambda plan, now: type("V", (), {"ok": True, "findings": []})(),
        planner_cfg=lambda: None,
        summer_cfg=lambda soc: None,
        adaptive_cfg=lambda: None,
    )
    return svc, ctx


def test_restart_mid_commitment_resumes_charge_when_live():
    """After #127 AUTO restore, a still-valid commitment re-applies CHARGE on a live boot."""
    driver = _WritesDriver(PhysicalMode.AUTO, armed=True)  # post-shutdown_restore
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    ctl = ModeController(driver, lc, dry_run=False)
    ctl.last_confirmed_action = PhysicalMode.AUTO
    ctl.set_charge_commitment(_commitment(target_soc=70.0, power_w=4000.0))
    # Mark readiness so we reach CONTROLLING without a real plan.
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    svc, _ = _service(ctl, dry_run=False, soc=25.0)

    # Force CONTROLLING + inject resume via _apply (no plan → effective_intent returns None;
    # exercise the resume helper + decide path directly through resolve+decide).
    lc.tick(NOW)
    assert lc.state is OwnershipState.CONTROLLING
    records, intent, reason, tgt, pw = svc._apply_charge_commitment_to_intent(
        NOW, lc, None, None, None, None, False)
    assert intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    assert tgt == 70.0
    assert any(r["detail"]["outcome"] == "resumed" for r in records)
    assert "resuming GRID_CHARGE" in (reason or "")

    dec = ctl.decide(intent, NOW, target_soc=tgt, power_w=pw, commitment=True)
    assert dec.outcome == "applied"
    assert driver.writes[-1] is PhysicalMode.CHARGE
    assert ctl.charge_commitment is not None  # still active until target/deadline


def test_restart_mid_commitment_dry_run_aborts_with_reason_no_write():
    """Deploy into watch-only: commitment aborted explicitly; battery never written."""
    driver = _WritesDriver(PhysicalMode.AUTO, armed=True)
    lc = Lifecycle(dry_run=True, startup_grace_seconds=0.0)
    ctl = ModeController(driver, lc, dry_run=True)
    ctl.set_charge_commitment(_commitment())
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    svc, _ = _service(ctl, dry_run=True, soc=25.0)
    lc.tick(NOW)
    assert lc.state is OwnershipState.DRY_RUN
    assert lc.can_command(NOW) is False

    records = svc._resolve_charge_commitment(NOW, lc, commanding=False)
    assert ctl.charge_commitment is None
    assert any(r["detail"]["outcome"] == "aborted" for r in records)
    assert any(ABORT_DRY_RUN in r["detail"]["reason"] for r in records)
    assert driver.writes == []


def test_restart_mid_commitment_unsafe_aborts_with_reason():
    driver = _WritesDriver(PhysicalMode.AUTO, armed=True)
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    ctl = ModeController(driver, lc, dry_run=False)
    ctl.set_charge_commitment(_commitment())
    lc.start(NOW)
    svc, _ = _service(ctl, dry_run=False, soc=25.0, data_quality="unsafe")
    records = svc._resolve_charge_commitment(NOW, lc, commanding=False)
    assert ctl.charge_commitment is None
    assert any(ABORT_UNSAFE in r["detail"]["reason"] for r in records)


def test_expired_commitment_aborts_on_boot():
    driver = _WritesDriver(PhysicalMode.AUTO, armed=True)
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    ctl = ModeController(driver, lc, dry_run=False)
    ctl.set_charge_commitment(_commitment(deadline=NOW - timedelta(minutes=5)))
    lc.start(NOW)
    svc, _ = _service(ctl, dry_run=False, soc=25.0)
    records, intent, *_ = svc._apply_charge_commitment_to_intent(
        NOW, lc, BatteryIntent.ALLOW_SELF_CONSUMPTION, "plan", None, None, False)
    assert ctl.charge_commitment is None
    assert intent is BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert any(ABORT_EXPIRED in r["detail"]["reason"] for r in records)


def test_target_reached_clears_commitment():
    driver = _WritesDriver(PhysicalMode.CHARGE, armed=True)
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    ctl = ModeController(driver, lc, dry_run=False)
    ctl.set_charge_commitment(_commitment(target_soc=50.0))
    lc.start(NOW)
    svc, _ = _service(ctl, dry_run=False, soc=55.0)
    records, *_ = svc._apply_charge_commitment_to_intent(
        NOW, lc, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge", 50.0, 4000.0, False)
    assert ctl.charge_commitment is None
    assert any(ABORT_TARGET_REACHED in r["detail"]["reason"] for r in records)


def test_persist_charge_commitment_from_service():
    driver = _WritesDriver(PhysicalMode.AUTO, armed=True)
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    ctl = ModeController(driver, lc, dry_run=False)
    svc, _ = _service(ctl, dry_run=False, soc=20.0)
    # No plan → deadline falls back to now+4h.
    svc._persist_charge_commitment(NOW, 65.0, 4800.0, "cheap midday")
    assert ctl.charge_commitment is not None
    assert ctl.charge_commitment.target_soc == 65.0
    assert ctl.charge_commitment.deadline == NOW + timedelta(hours=4)


def test_shutdown_restore_audit_preserves_commitment_flag():
    """Lifespan shutdown keeps commitment in controller state (interaction with #127)."""
    from fastapi.testclient import TestClient

    from ems.sources.mock import MockSource
    from ems.sources.prices import MockPriceSource
    from ems.web.api import create_app

    driver = _WritesDriver(PhysicalMode.CHARGE, armed=True)
    ctl = ModeController(driver, Lifecycle(dry_run=False), dry_run=False)
    ctl.last_confirmed_action = PhysicalMode.CHARGE
    ctl.set_charge_commitment(_commitment())
    app = create_app(
        MockSource(), dry_run=False, dev_mode="live", tz=AMS,
        price_source=MockPriceSource(AMS), controller=ctl,
        control_cycle_seconds=3600,
    )
    with TestClient(app):
        pass  # graceful shutdown → AUTO
    assert driver.writes[-1] is PhysicalMode.AUTO
    # Commitment must still be present after #127 restore (resume/abort on next boot).
    assert ctl.charge_commitment is not None
    assert ctl.charge_commitment.target_soc == 80.0
    assert ctl.last_confirmed_action is PhysicalMode.AUTO
