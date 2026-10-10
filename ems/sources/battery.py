"""The battery driver — the ONLY writer to the battery (SPEC §6.5). `BatteryDriver` is the port;
`MockBatteryDriver` is a CapabilityReport-driven in-memory Indevolt for dev/tests (no HA/hardware).

`intent_to_mode` maps a planner BatteryIntent to the physical mode the controller commands.
Per SPEC §8.3, "serve load during a peak" is really vendor self-consumption, so DISCHARGE_FOR_LOAD
maps to AUTO **always**. Forced DISCHARGE for deliberate grid export is reserved for
`EXPORT_FOR_PROFIT` and only when `allow_export_discharge=True` (E-11 / B-105). P1-zeroing stays
the vendor's job (SPEC §2) — the EMS never tries to track instantaneous power.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ems.domain import BatteryIntent, CapabilityReport, PhysicalMode

from .ports import BatteryDriver

__all__ = [
    "BatteryDriver", "BatteryWriteUnconfirmed", "MockBatteryDriver",
    "FailingMockBatteryDriver", "intent_to_mode",
]

# Optional spy/write hook for hermetic arming tests (#139 / I3). Not used in production wiring.
WriteTransport = Callable[..., Any]


class BatteryWriteUnconfirmed(Exception):
    """A write could NOT be confirmed because the device was slow/unreachable (transport timeout),
    as opposed to genuinely REJECTING the command (result:false). The two must be handled
    differently: a rejection means fall back to AUTO; a transport timeout means the write was very
    likely received (the device switches with latency) — so DON'T revert (the revert write would
    also time out and leave the cluster in a half-known state, alerting), just hold and re-verify
    next cycle. Raised by a real driver's apply(); never for a clean accept or a clean rejection."""

_INTENT_TO_MODE: dict[BatteryIntent, PhysicalMode] = {
    BatteryIntent.ALLOW_SELF_CONSUMPTION: PhysicalMode.AUTO,
    BatteryIntent.GRID_CHARGE_TO_TARGET: PhysicalMode.CHARGE,
    BatteryIntent.HOLD_RESERVE: PhysicalMode.IDLE,
    BatteryIntent.DISCHARGE_FOR_LOAD: PhysicalMode.AUTO,
}


def intent_to_mode(
    intent: BatteryIntent, *, allow_export_discharge: bool = False, car_session: bool = False,
) -> PhysicalMode:
    """Map a planner intent to the physical mode to command.

    DISCHARGE_FOR_LOAD always serves the house via vendor self-consumption (AUTO). It never
    becomes a forced DISCHARGE via `allow_export_discharge` — that flag arms **EXPORT_FOR_PROFIT
    only** (SPEC §7.1 / E-11). Defaulting DFL to AUTO keeps load-arbitrage fail-safe so a control
    loop can't export by accident.

    EXPORT_FOR_PROFIT → DISCHARGE only when `allow_export_discharge` is set; otherwise AUTO
    (planner should not emit it when disarmed; mapping still fail-safes).

    `car_session=True` is the ONE narrow, deliberate exception (feat/car-charge-modes): while the
    car is charging and the operator picked a discharge behaviour, DISCHARGE_FOR_LOAD becomes a real
    forced DISCHARGE (at the caller's bounded setpoint) EVEN when `allow_export_discharge` is False.
    Why this is safe-enough:
      * the setpoint is clamped ≤ `max_discharge_w` and to ~the predicted non-EV house load
        (ems.control.car_mode), so the battery covers the HOUSE, not the car — any export is either
        the small, explicit overshoot the operator opted into (static mode) or ≈0 (match mode);
      * the car session is re-evaluated EVERY control cycle, so a stopped car ends this DISCHARGE
        within one cycle; the only export exposure is the brief window between the car stopping and
        the next cycle — bounded and accepted;
      * nothing else is loosened — the §8.11 validator, the reserve floor and the single writer all
        stay in force. (SPEC §7.1's note on this was updated in iteration 3; not touched here.)
    """
    if intent is BatteryIntent.DISCHARGE_FOR_LOAD:
        if car_session:
            return PhysicalMode.DISCHARGE
        return PhysicalMode.AUTO
    if intent is BatteryIntent.EXPORT_FOR_PROFIT:
        return PhysicalMode.DISCHARGE if allow_export_discharge else PhysicalMode.AUTO
    return _INTENT_TO_MODE[intent]


class MockBatteryDriver:
    """Fake Indevolt: a SolidFlex-2000-shaped cluster. `apply` is idempotent and self-confirms.
    It records the last commanded target/power so tests can assert the energy contract was passed
    through, but (being a mode-only mock) it does not require a target to confirm.

    `armed` defaults False (same refuse-by-default as IndevoltBatteryDriver). Tests that exercise
    operational/shutdown/startup writes pass ``armed=True`` explicitly.

    Optional `write_transport` is a spy hook for hermetic I3 arming tests (#139): when injected,
    `apply` refuses (no transport call) unless armed — matching Indevolt's triple-gate. Without a
    transport (the default / existing unit-test path), in-memory mode updates still succeed so the
    large ModeController suite stays behaviour-compatible.
    """

    def __init__(
        self, *, armed: bool = False, write_transport: WriteTransport | None = None,
    ) -> None:
        self._armed = armed
        self._write_transport = write_transport
        self._mode = PhysicalMode.AUTO
        self.last_target_soc: float | None = None
        self.last_power_w: float | None = None
        # Fake Indevolt: same OpenData floors (50 W / 5% SoC); mock ceiling stays 4 kW for
        # ModeController suite power budgets (#112b vendor-neutral fields).
        self._capabilities = CapabilityReport(
            services=("charge", "discharge"),
            energy_mode_options=("self_consumed_prioritized", "real_time_control"),
            has_standby=True,
            has_grid_charge_switch=True,
            p1_paired=True,
            max_charge_w=4000.0,
            max_discharge_w=4000.0,
            supports_discharge_control=True,
            supports_grid_charge=True,
            supports_standby=True,
            min_power_w=50.0,
            min_target_soc=5.0,
        )

    @property
    def armed(self) -> bool:
        return self._armed

    def probe(self) -> CapabilityReport:
        return self._capabilities

    def configure_power_limits(self, *, max_charge_w: float, max_discharge_w: float) -> None:
        self._capabilities = replace(
            self._capabilities,
            max_charge_w=float(max_charge_w),
            max_discharge_w=float(max_discharge_w),
        )

    def current_mode(self) -> PhysicalMode:
        return self._mode

    def apply(
        self, mode: PhysicalMode, *, target_soc: float | None = None,
        power_w: float | None = None,
    ) -> bool:
        # I3 spy path: when a write_transport is injected, honour armed like Indevolt
        # (unarmed → refuse, zero transport calls). Default (no transport) keeps the in-memory
        # self-confirming mock used by ModeController unit tests.
        if self._write_transport is not None:
            if not self._armed:
                return False
            self._write_transport(mode, target_soc=target_soc, power_w=power_w)
        # Idempotent: re-applying the current mode is a no-op but still "confirmed".
        self._mode = mode
        self.last_target_soc, self.last_power_w = target_soc, power_w
        return True


class FailingMockBatteryDriver(MockBatteryDriver):
    """Test double whose apply() returns False (unconfirmed) for the first `fail_times` calls
    WITHOUT changing the mode — for exercising the failure/recovery path (SPEC §6.5)."""

    def __init__(self, fail_times: int = 1) -> None:
        super().__init__()
        self._remaining = fail_times

    def apply(
        self, mode: PhysicalMode, *, target_soc: float | None = None,
        power_w: float | None = None,
    ) -> bool:
        if self._remaining > 0:
            self._remaining -= 1
            return False  # command not confirmed; mode unchanged
        return super().apply(mode, target_soc=target_soc, power_w=power_w)
