"""#410: summer_cfg and replay _summer_cfg pass user wear/risk like adaptive_cfg / planner_cfg."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.clock import FrozenClock
from ems.control.mode_controller import ModeController
from ems.control.service import ControlContext, ControlService
from ems.domain import PhysicalMode
from ems.lifecycle import Lifecycle
from ems.planner.economics import max_buy_for_peak, max_buy_for_planner_config
from ems.planner.validator import PlanValidation
from ems.replay import ReplayConfig, _summer_cfg
from ems.settings import effective_settings
from ems.sources.battery import MockBatteryDriver

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
PEAK_EUR = 0.40
DEFAULT_DEG, DEFAULT_RISK = 0.05, 0.02
USER_DEG, USER_RISK = 0.07, 0.03


def _controlling_controller() -> ModeController:
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(NOW)
    return ModeController(MockBatteryDriver(), lc, dry_run=False)


def _service(overrides: dict) -> ControlService:
    settings = effective_settings(overrides)
    return ControlService(
        ctx=ControlContext(),
        settings=settings,
        controller=_controlling_controller(),
        store=None,
        audit_store=None,
        price_source=None,
        solar_forecast=None,
        site_tz=ZoneInfo("Europe/Amsterdam"),
        dry_run=False,
        clock=FrozenClock(NOW),
        current_soc=lambda now: 50.0,
        current_mode=lambda now: PhysicalMode.AUTO,
        current_towers=lambda now: None,
        data_quality=lambda now: "fresh",
        car_charging=lambda now: False,
        load_by=lambda starts: {s: 0.0 for s in starts},
        active_strategy=lambda now: "summer",
        validate_plan_obj=lambda plan, now: PlanValidation(status="valid"),
        planner_cfg=lambda: None,
        summer_cfg=lambda soc: None,
        adaptive_cfg=lambda: None,
    )


def test_summer_cfg_and_replay_carry_user_degradation_eur_per_kwh_and_risk_margin():
    """AC1: deg/risk in settings reach summer_cfg and ReplayConfig/_summer_cfg."""
    for deg, risk in ((DEFAULT_DEG, DEFAULT_RISK), (USER_DEG, USER_RISK)):
        overrides = {
            "planner.degradation_eur_per_kwh": deg,
            "planner.risk_margin_eur_per_kwh": risk,
        }
        svc = _service(overrides)
        summer = svc.summer_cfg(50.0)
        assert summer.degradation_eur_per_kwh == deg
        assert summer.risk_margin_eur_per_kwh == risk

        cfg = ReplayConfig.from_settings(overrides)
        replay_summer = _summer_cfg(cfg)
        assert replay_summer.degradation_eur_per_kwh == deg
        assert replay_summer.risk_margin_eur_per_kwh == risk
        assert cfg.degradation_eur_per_kwh == deg
        assert cfg.risk_margin_eur_per_kwh == risk


def test_summer_cfg_max_buy_matches_adaptive_cfg_for_peak_eur():
    """AC2: max_buy from summer_cfg equals live planner_cfg / adaptive_cfg for peak €0.40."""
    overrides = {
        "planner.degradation_eur_per_kwh": USER_DEG,
        "planner.risk_margin_eur_per_kwh": USER_RISK,
        "planner.round_trip_efficiency": 0.90,
    }
    svc = _service(overrides)
    summer = svc.summer_cfg(50.0)
    planner = svc.planner_cfg()
    adaptive = svc.adaptive_cfg()
    expected = max_buy_for_peak(PEAK_EUR, rte=0.90, deg=USER_DEG, risk=USER_RISK)
    assert max_buy_for_planner_config(PEAK_EUR, summer) == expected
    assert max_buy_for_planner_config(PEAK_EUR, planner) == expected
    assert max_buy_for_planner_config(PEAK_EUR, adaptive) == expected
    replay = _summer_cfg(ReplayConfig.from_settings(overrides))
    assert max_buy_for_planner_config(PEAK_EUR, replay) == expected


def test_summer_cfg_without_settings_uses_default_wear_risk_unchanged():
    """AC3: without_settings — summer_cfg / _summer_cfg keep default wear/risk unchanged."""
    svc = _service({})
    summer = svc.summer_cfg(50.0)
    assert summer.degradation_eur_per_kwh == DEFAULT_DEG
    assert summer.risk_margin_eur_per_kwh == DEFAULT_RISK
    replay = _summer_cfg(ReplayConfig.from_settings({}))
    assert replay.degradation_eur_per_kwh == DEFAULT_DEG
    assert replay.risk_margin_eur_per_kwh == DEFAULT_RISK


def test_service_summer_cfg_takes_planner_degradation_eur_per_kwh_from_settings():
    """PO: service.summer_cfg reads planner.degradation_eur_per_kwh / risk from self._settings."""
    svc = _service({
        "planner.degradation_eur_per_kwh": USER_DEG,
        "planner.risk_margin_eur_per_kwh": USER_RISK,
    })
    got = svc.summer_cfg(40.0)
    assert got.degradation_eur_per_kwh == svc._settings["planner.degradation_eur_per_kwh"]
    assert got.risk_margin_eur_per_kwh == svc._settings["planner.risk_margin_eur_per_kwh"]
