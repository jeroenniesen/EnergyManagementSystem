"""The mode controller (SPEC §6.5/§13): turns a planner BatteryIntent into at most one battery
write, gated for safety — dry-run, ownership (only commands while CONTROLLING), idempotency,
minimum dwell, a daily switch cap (reset at local midnight), and a failure→AUTO recovery.

`preview()` is read-only (for GET /api/decision — NEVER writes). `decide()` is the write path and
the ONLY caller of BatteryDriver.apply; it belongs to the control loop, not an HTTP GET.
NOTE: switches_today/last_switch_at are in-memory; SPEC §13.3 wants them persisted across
restarts (runtime_state.py) — a documented follow-up.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import BatteryDriver, intent_to_mode


@dataclass(frozen=True)
class ActionDecision:
    intent: BatteryIntent
    desired_mode: PhysicalMode
    applied: bool
    # dry_run | not_controlling | idempotent | dwell | cap_reached | would_apply |
    # applied | failed_recovered | failed_unrecovered
    outcome: str
    reason: str


class ModeController:
    def __init__(
        self,
        driver: BatteryDriver,
        lifecycle: Lifecycle,
        *,
        dry_run: bool,
        allow_export_discharge: bool = False,
        max_switches_per_day: int = 10,
        min_dwell_seconds: float = 600.0,
        tz: ZoneInfo | None = None,
    ) -> None:
        self.driver = driver
        self.lifecycle = lifecycle
        self.dry_run = dry_run
        self.allow_export_discharge = allow_export_discharge
        self.max_switches_per_day = max_switches_per_day
        self.min_dwell = timedelta(seconds=min_dwell_seconds)
        self.tz = tz or ZoneInfo("UTC")
        self.switches_today = 0
        self.last_switch_at: datetime | None = None
        self._counter_date: date | None = None

    def _desired(self, intent: BatteryIntent) -> PhysicalMode:
        return intent_to_mode(intent, allow_export_discharge=self.allow_export_discharge)

    def _effective_switches(self, now: datetime) -> int:
        """Today's switch count, treating a new local date as a fresh 0 (read-only)."""
        if self._counter_date is not None and self._counter_date != now.astimezone(self.tz).date():
            return 0
        return self.switches_today

    def _gate(
        self, intent: BatteryIntent, now: datetime, desired: PhysicalMode
    ) -> ActionDecision | None:
        """Return a blocking ActionDecision, or None if a write should proceed. No side effects."""
        if self.dry_run:
            return ActionDecision(
                intent, desired, False, "dry_run", f"dry-run: would set {desired}"
            )
        if not self.lifecycle.can_command(now):
            return ActionDecision(
                intent, desired, False, "not_controlling",
                f"not commanding (state={self.lifecycle.state})",
            )
        if desired == self.driver.current_mode():
            return ActionDecision(intent, desired, False, "idempotent", f"already in {desired}")
        if self.last_switch_at is not None and now - self.last_switch_at < self.min_dwell:
            return ActionDecision(intent, desired, False, "dwell", "min dwell not elapsed; holding")
        if self._effective_switches(now) >= self.max_switches_per_day:
            return ActionDecision(
                intent, desired, False, "cap_reached", "daily switch cap reached; holding"
            )
        return None

    def preview(self, intent: BatteryIntent, now: datetime) -> ActionDecision:
        """Read-only: what decide() WOULD do right now. Never writes or mutates state."""
        desired = self._desired(intent)
        blocked = self._gate(intent, now, desired)
        if blocked is not None:
            return blocked
        return ActionDecision(intent, desired, False, "would_apply", f"would set {desired}")

    def decide(self, intent: BatteryIntent, now: datetime) -> ActionDecision:
        """Write path: applies at most one mode change. The ONLY caller of driver.apply."""
        desired = self._desired(intent)
        blocked = self._gate(intent, now, desired)
        if blocked is not None:
            return blocked

        self._reset_counter_if_new_day(now)
        if not self.driver.apply(desired):
            # Unconfirmed -> revert to the safe vendor mode (SPEC §6.5). Check recovery too.
            recovered = self.driver.apply(PhysicalMode.AUTO)
            outcome = "failed_recovered" if recovered else "failed_unrecovered"
            reason = (
                f"{desired} unconfirmed -> reverted to AUTO"
                if recovered
                else f"{desired} unconfirmed AND AUTO recovery unconfirmed — ALERT"
            )
            return ActionDecision(intent, PhysicalMode.AUTO, recovered, outcome, reason)
        self.switches_today += 1
        self.last_switch_at = now
        return ActionDecision(intent, desired, True, "applied", f"set {desired}")

    def _reset_counter_if_new_day(self, now: datetime) -> None:
        today = now.astimezone(self.tz).date()
        if self._counter_date != today:
            self.switches_today = 0
            self._counter_date = today
