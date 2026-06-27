"""Ownership state machine + boot sequence + startup grace (SPEC §13.3/§13.4).

Boot: INACTIVE -> OBSERVING (observe only). Once sensors are validated, the capability
probe has run, a plan is loaded, AND the startup grace has elapsed, the EMS advances to
DRY_RUN (if dry_run) or CONTROLLING. MANUAL_OVERRIDE is an overlay that blocks commanding
until it expires. The EMS commands the battery ONLY in CONTROLLING.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class OwnershipState(StrEnum):
    INACTIVE = "inactive"
    OBSERVING = "observing"
    DRY_RUN = "dry_run"
    CONTROLLING = "controlling"
    MANUAL_OVERRIDE = "manual_override"


@dataclass
class Lifecycle:
    dry_run: bool
    startup_grace_seconds: float = 120.0
    boot_at: datetime | None = None
    state: OwnershipState = OwnershipState.INACTIVE
    _sensors_ok: bool = False
    _probe_ok: bool = False
    _plan_loaded: bool = False
    _override_until: datetime | None = None

    # --- boot sequence (observe first, validate, load plan, then maybe act) ---
    def start(self, now: datetime) -> None:
        self.boot_at = now
        self.state = OwnershipState.OBSERVING

    def mark_sensors_validated(self) -> None:
        self._sensors_ok = True

    def mark_probe_ok(self) -> None:
        self._probe_ok = True

    def mark_plan_loaded(self) -> None:
        self._plan_loaded = True

    # --- grace + readiness ---
    def grace_elapsed(self, now: datetime) -> bool:
        if self.boot_at is None:
            return False
        return (now - self.boot_at).total_seconds() >= self.startup_grace_seconds

    def ready_to_act(self, now: datetime) -> bool:
        return (
            self._sensors_ok
            and self._probe_ok
            and self._plan_loaded
            and self.grace_elapsed(now)
            and not self.override_active(now)
        )

    # --- manual override overlay ---
    def manual_override(self, now: datetime, duration_s: float) -> None:
        self._override_until = now + timedelta(seconds=duration_s)
        self.state = OwnershipState.MANUAL_OVERRIDE

    def override_active(self, now: datetime) -> bool:
        return self._override_until is not None and now < self._override_until

    def return_to_default(self) -> None:
        """Emergency 'return to Indevolt default': clear any override, drop to OBSERVING."""
        self._override_until = None
        self.state = OwnershipState.OBSERVING

    # --- the per-cycle advance ---
    def tick(self, now: datetime) -> OwnershipState:
        # Expire a lapsed override first.
        if self._override_until is not None and now >= self._override_until:
            self._override_until = None
            if self.state is OwnershipState.MANUAL_OVERRIDE:
                self.state = OwnershipState.OBSERVING
        if self.state is OwnershipState.OBSERVING and self.ready_to_act(now):
            self.state = OwnershipState.DRY_RUN if self.dry_run else OwnershipState.CONTROLLING
        return self.state

    def can_command(self, now: datetime) -> bool:
        """True only when actively controlling — never in dry-run, observing, or override."""
        return self.state is OwnershipState.CONTROLLING and not self.override_active(now)
