"""The mode controller (SPEC §6.5/§13): turns a planner BatteryIntent into at most one battery
write, gated for safety — dry-run, ownership (only commands while CONTROLLING), idempotency,
minimum dwell, a daily switch cap (reset at local midnight), and a failure→AUTO recovery.

`preview()` is read-only (for GET /api/decision — NEVER writes). `decide()` is the write path and
the ONLY caller of BatteryDriver.apply; it belongs to the control loop, not an HTTP GET.
NOTE: switches_today/last_switch_at are in-memory; SPEC §13.3 wants them persisted across
restarts (runtime_state.py) — a documented follow-up.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import BatteryDriver, intent_to_mode


def _mode_or_none(value: object) -> PhysicalMode | None:
    """A PhysicalMode from its stored value, or None for empty/unknown (tolerant of bad blobs)."""
    if not value:
        return None
    try:
        return PhysicalMode(value)
    except ValueError:
        return None


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
        on_state_change: Callable[[dict], None] | None = None,
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
        # Persisted across restarts (SPEC §13.3) so dwell + the daily cap survive a reboot, and so
        # we can hand the battery back to the mode it was in before EMS took control.
        self.last_requested_action: PhysicalMode | None = None
        self.last_confirmed_action: PhysicalMode | None = None
        self.original_vendor_mode: PhysicalMode | None = None
        # Called (with state_snapshot()) whenever persistable state changes; the caller persists it.
        self._on_state_change = on_state_change

    def state_snapshot(self) -> dict:
        """JSON-serialisable persistable control state (counters, dwell, last actions, vendor mode).
        Modes are stored as their PhysicalMode value; datetimes/dates as ISO strings."""
        return {
            "switches_today": self.switches_today,
            "last_switch_at": self.last_switch_at.isoformat() if self.last_switch_at else None,
            "counter_date": self._counter_date.isoformat() if self._counter_date else None,
            "last_requested_action": self.last_requested_action.value
            if self.last_requested_action else None,
            "last_confirmed_action": self.last_confirmed_action.value
            if self.last_confirmed_action else None,
            "original_vendor_mode": self.original_vendor_mode.value
            if self.original_vendor_mode else None,
        }

    def restore_state(self, state: dict | None) -> None:
        """Load a state_snapshot() (e.g. at startup) — tolerant of missing/garbage fields."""
        if not state:
            return
        try:
            self.switches_today = int(state.get("switches_today") or 0)
            lsa = state.get("last_switch_at")
            self.last_switch_at = datetime.fromisoformat(lsa) if lsa else None
            cd = state.get("counter_date")
            self._counter_date = date.fromisoformat(cd) if cd else None
            self.last_requested_action = _mode_or_none(state.get("last_requested_action"))
            self.last_confirmed_action = _mode_or_none(state.get("last_confirmed_action"))
            self.original_vendor_mode = _mode_or_none(state.get("original_vendor_mode"))
        except (ValueError, TypeError):
            pass  # a corrupt blob must not crash startup — start from a clean in-memory state

    def _persist(self) -> None:
        if self._on_state_change is not None:
            try:
                self._on_state_change(self.state_snapshot())
            except Exception:
                pass  # persistence is best-effort; never fail a control decision over it

    def _desired(self, intent: BatteryIntent) -> PhysicalMode:
        return intent_to_mode(intent, allow_export_discharge=self.allow_export_discharge)

    def _effective_switches(self, now: datetime) -> int:
        """Today's switch count, treating a new local date as a fresh 0 (read-only)."""
        if self._counter_date is not None and self._counter_date != now.astimezone(self.tz).date():
            return 0
        return self.switches_today

    def _gate(
        self, intent: BatteryIntent, now: datetime, desired: PhysicalMode,
        *, observed_mode: PhysicalMode | None = None,
    ) -> ActionDecision | None:
        """Return a blocking ActionDecision, or None if a write should proceed. No side effects.
        `observed_mode`, when supplied (by the read-only UI preview), is used for the idempotency
        check INSTEAD of reading the device — so a dashboard poll doesn't add a battery mode-read
        every cycle. The write path (decide) passes None and always reads fresh hardware."""
        if self.dry_run:
            return ActionDecision(
                intent, desired, False, "dry_run", f"dry-run: would set {desired}"
            )
        if not self.lifecycle.can_command(now):
            return ActionDecision(
                intent, desired, False, "not_controlling",
                f"not commanding (state={self.lifecycle.state})",
            )
        current = observed_mode if observed_mode is not None else self.driver.current_mode()
        if desired == current:
            return ActionDecision(intent, desired, False, "idempotent", f"already in {desired}")
        if self.last_switch_at is not None and now - self.last_switch_at < self.min_dwell:
            return ActionDecision(intent, desired, False, "dwell", "min dwell not elapsed; holding")
        if self._effective_switches(now) >= self.max_switches_per_day:
            return ActionDecision(
                intent, desired, False, "cap_reached", "daily switch cap reached; holding"
            )
        return None

    def preview(
        self, intent: BatteryIntent, now: datetime, *,
        target_soc: float | None = None, power_w: float | None = None,
        observed_mode: PhysicalMode | None = None,
    ) -> ActionDecision:
        """Read-only: what decide() WOULD do right now. Never writes or mutates state. Pass
        `observed_mode` (a recently-observed mode) to avoid a hardware mode-read per call."""
        desired = self._desired(intent)
        blocked = self._gate(intent, now, desired, observed_mode=observed_mode)
        if blocked is not None:
            return blocked
        suffix = (f" to {target_soc:.0f}%"
                  if target_soc is not None
                  and desired in (PhysicalMode.CHARGE, PhysicalMode.DISCHARGE) else "")
        return ActionDecision(intent, desired, False, "would_apply", f"would set {desired}{suffix}")

    def decide(
        self, intent: BatteryIntent, now: datetime, *,
        target_soc: float | None = None, power_w: float | None = None,
        observed_mode: PhysicalMode | None = None,
    ) -> ActionDecision:
        """Write path: applies at most one mode change. The ONLY caller of driver.apply. The plan's
        target SoC + power are passed through to the driver (which refuses a target-less charge).
        `observed_mode` (a recently-observed mode, e.g. from the shared coalesced cluster read) is
        used for the idempotency gate so the control loop needn't read the device every cycle; the
        post-write CONFIRM still re-reads fresh, so a stale observation can at worst cause one
        redundant (idempotent) write, never an unconfirmed change."""
        desired = self._desired(intent)
        blocked = self._gate(intent, now, desired, observed_mode=observed_mode)
        if blocked is not None:
            return blocked

        self._reset_counter_if_new_day(now)
        # Capture the mode the battery was in BEFORE EMS first took control (to hand it back later).
        if self.original_vendor_mode is None:
            try:
                self.original_vendor_mode = self.driver.current_mode()
            except Exception:
                pass
        self.last_requested_action = desired
        if not self.driver.apply(desired, target_soc=target_soc, power_w=power_w):
            # Unconfirmed -> revert to the safe vendor mode (SPEC §6.5). Check recovery too.
            recovered = self.driver.apply(PhysicalMode.AUTO)
            outcome = "failed_recovered" if recovered else "failed_unrecovered"
            reason = (
                f"{desired} unconfirmed -> reverted to AUTO"
                if recovered
                else f"{desired} unconfirmed AND AUTO recovery unconfirmed — ALERT"
            )
            self.last_confirmed_action = PhysicalMode.AUTO if recovered else None
            # A failed/unconfirmed write still hit the device with SetData POSTs, so it MUST count
            # like a switch: start the dwell timer and the daily cap. Without this, a write that
            # never confirms (e.g. a flaky/half-offline tower) was retried every single control
            # cycle forever — write-amplification into already-struggling hardware. Now the dwell
            # gate spaces retries out and the daily cap stops them entirely after the budget.
            self.switches_today += 1
            self.last_switch_at = now
            self._persist()
            return ActionDecision(intent, PhysicalMode.AUTO, recovered, outcome, reason)
        self.switches_today += 1
        self.last_switch_at = now
        self.last_confirmed_action = desired
        self._persist()
        return ActionDecision(intent, desired, True, "applied", f"set {desired}")

    def _reset_counter_if_new_day(self, now: datetime) -> None:
        today = now.astimezone(self.tz).date()
        if self._counter_date != today:
            self.switches_today = 0
            self._counter_date = today
