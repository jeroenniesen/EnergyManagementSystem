from datetime import UTC, datetime

from ems.application.protocols import BatteryController, DataQuality, PlanProvider, PlanValidator
from ems.control.command_fence import BatteryCommandFence
from ems.control.execution import CommandExecutionBoundary
from ems.control.safety import SafetyValidator
from ems.domain import PhysicalMode


def test_callback_protocols_accept_existing_closures() -> None:
    now = datetime.now(UTC)

    quality: DataQuality

    def quality_fn(at: datetime) -> str:
        return "fresh" if at == now else "unsafe"
    quality = quality_fn

    validator: PlanValidator

    def validator_fn(plan: object, at: datetime) -> object:
        return plan
    validator = validator_fn

    provider: PlanProvider

    def provider_fn() -> None:
        return None
    provider = provider_fn
    assert quality(now) == "fresh"
    assert validator("plan", now) == "plan"
    assert provider() is None
    assert SafetyValidator(data_quality=quality, validate_plan=validator).data_is_safe(now)


def test_controller_protocol_preserves_fenced_driver_boundary() -> None:
    class Driver:
        def apply(self, mode: PhysicalMode) -> bool:
            return mode is PhysicalMode.AUTO

    class Controller:
        driver = Driver()

        def decide(self, *args, **kwargs):
            return "ok"

    controller: BatteryController = Controller()
    boundary = CommandExecutionBoundary(controller, BatteryCommandFence())
    assert boundary.apply(PhysicalMode.AUTO)
