from datetime import UTC, datetime

from ems.application.protocols import BatteryController, DataQuality, PlanProvider, PlanValidator
from ems.control.command_fence import BatteryCommandFence
from ems.control.execution import CommandExecutionBoundary
from ems.control.mode_controller import ActionDecision
from ems.control.safety import SafetyValidator
from ems.domain import BatteryIntent, PhysicalMode
from ems.planner.schedule import Plan
from ems.planner.validator import PlanValidation


def test_callback_protocols_accept_existing_closures() -> None:
    now = datetime.now(UTC)

    quality: DataQuality

    def quality_fn(now: datetime) -> str:
        return "fresh"
    quality = quality_fn

    validator: PlanValidator

    def validator_fn(plan: Plan, now: datetime) -> PlanValidation:
        return PlanValidation("valid")
    validator = validator_fn

    provider: PlanProvider

    def provider_fn(now: datetime | None = None) -> None:
        return None
    provider = provider_fn
    assert quality(now) == "fresh"
    assert validator(Plan(created_at=now, slots=()), now).ok
    assert provider() is None
    assert SafetyValidator(data_quality=quality, validate_plan=validator).data_is_safe(now)


def test_controller_protocol_preserves_fenced_driver_boundary() -> None:
    class Driver:
        def apply(self, mode: PhysicalMode) -> bool:
            return mode is PhysicalMode.AUTO

    class Controller:
        driver = Driver()

        def decide(self, *args, **kwargs) -> ActionDecision:
            return ActionDecision(BatteryIntent.ALLOW_SELF_CONSUMPTION,
                                  PhysicalMode.AUTO, False, "dry_run", "test")

    controller: BatteryController = Controller()
    boundary = CommandExecutionBoundary(controller, BatteryCommandFence())
    assert boundary.apply(PhysicalMode.AUTO)


def test_plan_provider_exposes_time_and_full_plan_inputs():
    from inspect import signature
    from typing import get_type_hints

    from ems.sources.prices import PriceSlot

    hints = get_type_hints(PlanProvider.__call__)
    assert hints["return"] == tuple[datetime, list[PriceSlot], Plan] | None
    assert hints["now"] == datetime | None
    assert signature(PlanProvider.__call__).parameters["now"].default is None


def test_controller_protocol_matches_actual_accepted_decision_arguments():
    from inspect import signature
    from typing import get_type_hints

    from ems.control.mode_controller import ModeController

    expected = signature(ModeController.decide).parameters
    actual = signature(BatteryController.decide).parameters
    assert actual.keys() == expected.keys()
    for name in expected:
        assert actual[name].kind == expected[name].kind
        assert actual[name].default == expected[name].default
    assert get_type_hints(BatteryController.decide) == get_type_hints(ModeController.decide)
