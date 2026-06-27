"""The mode controller (SPEC §6.5/§13): turns a planner BatteryIntent into at most one battery
write, gated for safety — dry-run, ownership (only commands while CONTROLLING), idempotency,
minimum dwell, a daily switch cap, and a failure→AUTO recovery. It is the ONLY caller of
BatteryDriver.apply. In dry-run it computes what it *would* do and why, but never writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import BatteryDriver, intent_to_mode


@dataclass(frozen=True)
class ActionDecision:
    intent: BatteryIntent
    desired_mode: PhysicalMode
    applied: bool
    # applied | dry_run | not_controlling | idempotent | dwell | cap_reached | failed_recovered
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
    ) -> None:
        self.driver = driver
        self.lifecycle = lifecycle
        self.dry_run = dry_run
        self.allow_export_discharge = allow_export_discharge
        self.max_switches_per_day = max_switches_per_day
        self.min_dwell = timedelta(seconds=min_dwell_seconds)
        self.switches_today = 0
        self.last_switch_at: datetime | None = None

    def decide(self, intent: BatteryIntent, now: datetime) -> ActionDecision:
        desired = intent_to_mode(intent, allow_export_discharge=self.allow_export_discharge)

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
        if self.switches_today >= self.max_switches_per_day:
            return ActionDecision(
                intent, desired, False, "cap_reached", "daily switch cap reached; holding"
            )

        if not self.driver.apply(desired):
            # Unconfirmed -> revert to the safe vendor mode (SPEC §6.5 failure path).
            self.driver.apply(PhysicalMode.AUTO)
            return ActionDecision(
                intent, PhysicalMode.AUTO, True, "failed_recovered",
                f"{desired} unconfirmed -> reverted to AUTO",
            )
        self.switches_today += 1
        self.last_switch_at = now
        return ActionDecision(intent, desired, True, "applied", f"set {desired}")
