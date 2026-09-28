"""Pure safety checks for the control decision path."""
from __future__ import annotations

from datetime import datetime, timedelta

from ems.application.protocols import DataQuality, Plan, PlanValidation, PlanValidator
from ems.control.failsafe import failsafe_intent
from ems.domain import BatteryIntent, PhysicalMode

# Floor anti-flap (#165 / SPEC §8.8): HOLD_RESERVE↔ALLOW_SELF_CONSUMPTION oscillation at/below
# min_reserve_soc burned the daily switch budget overnight (Mac Mini 2026-09-27: 8 idle↔auto
# switches at 5% SoC, then cap_reached on the cheap midday window). Intent-persistence alone is
# not enough because return-to-AUTO is exempt and acts immediately. Once latched, stay until SoC
# recovers this many pp above the reserve (same resume margin as the car-mode floor hysteresis).
_FLOOR_RESUME_PP = 3.0
_FLOOR_ROUTINE_INTENTS = frozenset({
    BatteryIntent.HOLD_RESERVE,
    BatteryIntent.ALLOW_SELF_CONSUMPTION,
})
_FLOOR_SAFE_MODES = frozenset({PhysicalMode.AUTO, PhysicalMode.IDLE})


def resolve_floor_anti_flap(
    intent: BatteryIntent,
    *,
    soc_pct: float | None,
    min_reserve_soc: float,
    observed_mode: PhysicalMode | None,
    holding: bool,
    resume_pp: float = _FLOOR_RESUME_PP,
) -> tuple[BatteryIntent, str | None, bool]:
    """Latch to one safe mode at/below the SoC floor — no hold_reserve↔AUTO oscillation (#165).

    Returns ``(effective_intent, reason_or_None, new_holding)``.

    **Choice (documented vs SPEC §7.1 intent table):** when already in ``AUTO`` or ``IDLE``,
    *stay* there (zero writes). When entering from a non-safe mode (e.g. charge just ended),
    prefer ``ALLOW_SELF_CONSUMPTION`` → ``AUTO`` over ``HOLD_RESERVE`` → ``IDLE``: at the floor
    both are discharge-safe (nothing left to protect by idling), AUTO matches the fail-safe
    ("never worse than vendor self-consumption") and still lets solar fill, and forcing IDLE
    would spend a switch for no SoC-protection gain. Commitments / overrides / non-routine
    intents are not remapped here (caller skips overrides; charge clears the latch).

    Unknown SoC (``None``, #134) never latches. Enter at ``soc ≤ min_reserve_soc``; resume when
    ``soc ≥ min_reserve_soc + resume_pp`` so noise around the floor cannot re-enable flapping.
    """
    if intent not in _FLOOR_ROUTINE_INTENTS:
        return intent, None, False
    if soc_pct is None:
        return intent, None, False

    floor = float(min_reserve_soc)
    in_band = (soc_pct < floor + resume_pp) if holding else (soc_pct <= floor)
    if not in_band:
        return intent, None, False

    if observed_mode is PhysicalMode.IDLE:
        latched, mode_name = BatteryIntent.HOLD_RESERVE, "idle"
    elif observed_mode is PhysicalMode.AUTO:
        latched, mode_name = BatteryIntent.ALLOW_SELF_CONSUMPTION, "auto"
    else:
        # Not yet in a floor-safe mode — prefer AUTO (see docstring).
        latched, mode_name = BatteryIntent.ALLOW_SELF_CONSUMPTION, "auto"

    if latched is intent and observed_mode in _FLOOR_SAFE_MODES:
        # Already aligned with the latched safe mode — quiet (idempotent decide downstream).
        return latched, None, True

    if observed_mode in _FLOOR_SAFE_MODES:
        reason = (
            f"SoC at reserve floor ({soc_pct:.0f}% ≤ {floor:.0f}%) — staying in {mode_name} "
            f"(floor anti-flap); not switching hold_reserve↔self-consumption"
        )
    else:
        reason = (
            f"SoC at reserve floor ({soc_pct:.0f}% ≤ {floor:.0f}%) — entering {mode_name} as "
            f"the single floor-safe mode (anti-flap; prefer AUTO over idle at the floor)"
        )
    return latched, reason, True


class SafetyValidator:
    """Safety façade; command admission and writer fencing remain in ModeController."""
    def __init__(
        self,
        *,
        data_quality: DataQuality,
        validate_plan: PlanValidator,
    ):
        self._data_quality = data_quality
        self._validate_plan = validate_plan
    def data_is_safe(self, now: datetime) -> bool:
        return self._data_quality(now) != "unsafe"
    def validate(self, plan: Plan, now: datetime) -> PlanValidation:
        return self._validate_plan(plan, now)
    def failsafe(self, intent: BatteryIntent, now: datetime) -> tuple[BatteryIntent, str | None]:
        return failsafe_intent(intent, self._data_quality(now))
    @staticmethod
    def reserve_reached(soc_pct: float, reserve_pct: float, *, margin_pp: float = 0.0) -> bool:
        return soc_pct <= reserve_pct + margin_pp
    @staticmethod
    def dwell_elapsed(now: datetime, last_at: datetime | None, dwell: timedelta) -> bool:
        return last_at is None or now - last_at >= dwell
    @staticmethod
    def switch_cap_reached(switches_today: int, max_switches: int) -> bool:
        return switches_today >= max_switches
