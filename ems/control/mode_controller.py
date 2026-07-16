"""The mode controller (SPEC §6.5/§13): turns a planner BatteryIntent into at most one battery
write, gated for safety — dry-run, ownership (only commands while CONTROLLING), idempotency,
minimum dwell, a daily switch cap (reset at local midnight), and a failure→AUTO recovery.

`preview()` is read-only (for GET /api/decision — NEVER writes). `decide()` is the write path and
the ONLY caller of BatteryDriver.apply; it belongs to the control loop, not an HTTP GET.
NOTE: switches_today/last_switch_at are in-memory; SPEC §13.3 wants them persisted across
restarts (runtime_state.py) — a documented follow-up.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import BatteryDriver, BatteryWriteUnconfirmed, intent_to_mode

log = logging.getLogger(__name__)


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
    # applied | failed_recovered | failed_unrecovered | unconfirmed
    outcome: str
    reason: str
    # F3 incident de-dupe: whether this decision is worth writing to the incident/audit trail as a
    # NEW row. True for every ordinary outcome; set False on REPEAT `unconfirmed` cycles within one
    # stuck episode so a single "charge isn't sticking" incident is logged once (then again only
    # after ~60 min), not inflated to a row every dwell cycle. Control behaviour is unaffected —
    # this only governs audit noise; the caller logs a new row when `audit` is True.
    audit: bool = True


class ModeController:
    def __init__(
        self,
        driver: BatteryDriver,
        lifecycle: Lifecycle,
        *,
        dry_run: bool,
        allow_export_discharge: bool = False,
        max_switches_per_day: int = 10,
        commitment_reserve: int = 0,
        min_dwell_seconds: float = 600.0,
        tz: ZoneInfo | None = None,
        on_state_change: Callable[[dict], None] | None = None,
    ) -> None:
        self.driver = driver
        self.lifecycle = lifecycle
        self.dry_run = dry_run
        self.allow_export_discharge = allow_export_discharge
        self.max_switches_per_day = max_switches_per_day
        # Anti-starvation split of the daily switch cap (07-12 guardrail-starvation incident: 13
        # routine auto<->idle flaps burned the 10-switch cap by 09:48, then 5 cap_reached blocks
        # starved a COMMITTED grid-charge — it missed its deadline by 66 min). ROUTINE switches may
        # consume at most (max - commitment_reserve); a committed grid-charge draws from the FULL
        # cap (routine remainder + this reserve); the daily TOTAL is still bounded by max (the
        # reserve carves out headroom, it never extends the cap). Defaults to 0 so a ModeController
        # built without it behaves exactly like the plain cap; production sets it from
        # control.commitment_reserve via _apply_control_settings.
        self.commitment_reserve = commitment_reserve
        self.min_dwell = timedelta(seconds=min_dwell_seconds)
        self.tz = tz or ZoneInfo("UTC")
        self.switches_today = 0
        # How many of today's switches were COMMITMENT writes (grid-charge). switches_today minus
        # this is the routine consumption checked against the routine budget. Reset with the cap.
        self.commitment_switches_today = 0
        self.last_switch_at: datetime | None = None
        self._counter_date: date | None = None
        # Persisted across restarts (SPEC §13.3) so dwell + the daily cap survive a reboot, and so
        # we can hand the battery back to the mode it was in before EMS took control.
        self.last_requested_action: PhysicalMode | None = None
        self.last_confirmed_action: PhysicalMode | None = None
        self.original_vendor_mode: PhysicalMode | None = None
        # F3 incident de-dupe: the currently-open "unconfirmed" episode, so a stuck intent audits
        # once per episode rather than every dwell cycle (one live episode inflated to 13 rows).
        # Keyed by (intent, desired-mode); `_at` is when the episode was first (or last re-)logged.
        self._unconfirmed_key: tuple[BatteryIntent, PhysicalMode] | None = None
        self._unconfirmed_logged_at: datetime | None = None
        # Called (with state_snapshot()) whenever persistable state changes; the caller persists it.
        self._on_state_change = on_state_change

    def state_snapshot(self) -> dict:
        """JSON-serialisable persistable control state (counters, dwell, last actions, vendor mode).
        Modes are stored as their PhysicalMode value; datetimes/dates as ISO strings."""
        return {
            "switches_today": self.switches_today,
            "commitment_switches_today": self.commitment_switches_today,
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
            self.commitment_switches_today = int(state.get("commitment_switches_today") or 0)
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
            except Exception as e:
                # Persistence is best-effort; never fail a control decision over it — but don't
                # swallow it silently (a broken store must be visible in the logs).
                log.warning("persist failed: %s", e)

    def _desired(self, intent: BatteryIntent, *, car_session: bool = False) -> PhysicalMode:
        return intent_to_mode(intent, allow_export_discharge=self.allow_export_discharge,
                              car_session=car_session)

    # F3: re-log a still-stuck episode at most this often, so a long outage still leaves periodic
    # evidence in the incident trail (not one row for hours, not a row every cycle).
    _UNCONFIRMED_RELOG = timedelta(minutes=60)

    def _note_unconfirmed_episode(
        self, intent: BatteryIntent, desired: PhysicalMode, now: datetime,
    ) -> bool:
        """Record that `(intent, desired)` just came back `unconfirmed` and return whether THIS one
        is audit-worthy (a NEW incident row). True for the first unconfirmed of an episode and again
        once >60 min have passed since it was last logged; False for repeats in between. An episode
        ends (and the next unconfirmed re-audits) when a write confirms or the intent/mode changes —
        see `_clear_unconfirmed_episode`, called from decide()'s applied/failed paths."""
        key = (intent, desired)
        if (self._unconfirmed_key != key or self._unconfirmed_logged_at is None
                or now - self._unconfirmed_logged_at >= self._UNCONFIRMED_RELOG):
            self._unconfirmed_key = key
            self._unconfirmed_logged_at = now
            return True
        return False  # same episode, within the re-log window → suppress the duplicate row

    def _clear_unconfirmed_episode(self) -> None:
        """End any open unconfirmed episode (a write confirmed, or was rejected and reverted to
        AUTO) so a subsequent unconfirmed is audited as a fresh incident."""
        self._unconfirmed_key = None
        self._unconfirmed_logged_at = None

    def _effective_switches(self, now: datetime) -> int:
        """Today's TOTAL switch count, treating a new local date as a fresh 0 (read-only)."""
        if self._counter_date is not None and self._counter_date != now.astimezone(self.tz).date():
            return 0
        return self.switches_today

    def _effective_commitment_switches(self, now: datetime) -> int:
        """Today's COMMITMENT switch count, treating a new local date as a fresh 0 (read-only) —
        so the routine budget is computed against the same day boundary as the total cap."""
        if self._counter_date is not None and self._counter_date != now.astimezone(self.tz).date():
            return 0
        return self.commitment_switches_today

    def _cap_block(
        self, intent: BatteryIntent, desired: PhysicalMode, now: datetime, *, commitment: bool,
    ) -> ActionDecision | None:
        """The daily-cap gate, split into routine vs commitment budgets (07-12 starvation fix).
        Returns a blocking `cap_reached` ActionDecision, or None to allow the write. The TOTAL is
        always bounded by max_switches_per_day; a routine switch is additionally bounded by
        (max - commitment_reserve) so idle/auto flapping can't consume the switches reserved for a
        committed grid-charge. The reason names WHICH budget ran out (explainability first)."""
        total = self._effective_switches(now)
        cap = self.max_switches_per_day
        if total >= cap:  # the whole daily cap is spent — nothing of either class gets through
            return ActionDecision(
                intent, desired, False, "cap_reached",
                f"daily switch cap reached ({total}/{cap} used); holding")
        if commitment:
            return None  # a commitment draws from the full cap (routine remainder + the reserve)
        routine_budget = max(0, cap - self.commitment_reserve)
        routine_used = total - self._effective_commitment_switches(now)
        if routine_used >= routine_budget:
            return ActionDecision(
                intent, desired, False, "cap_reached",
                f"routine switch budget exhausted ({routine_used}/{routine_budget} used, "
                f"{self.commitment_reserve} reserved for charge commitments); holding")
        return None

    def _gate(
        self, intent: BatteryIntent, now: datetime, desired: PhysicalMode,
        *, observed_mode: PhysicalMode | None = None, manual: bool = False,
        priority: bool = False, force: bool = False, commitment: bool = False,
    ) -> ActionDecision | None:
        """Return a blocking ActionDecision, or None if a write should proceed. No side effects.
        `observed_mode`, when supplied (by the read-only UI preview), is used for the idempotency
        check INSTEAD of reading the device — so a dashboard poll doesn't add a battery mode-read
        every cycle. The write path (decide) passes None and always reads fresh hardware.

        The dwell + daily-cap gates exist to limit the AUTOMATIC planner's churn (the "<10
        writes/day, mode-switching not continuous" rule). Three kinds of action BYPASS them:
        - `manual=True`  — an explicit operator override (a deliberate command, not churn);
        - `priority=True` — a SAFETY action, e.g. the car-guard hold: never drain the battery into
          the car just because today's switch budget is spent (a capped hold left it self-consuming
          into a 10 kW car — the live bug);
        - `desired is AUTO` — returning to safe vendor self-consumption is always allowed (the
          fail-safe can never be blocked, or an expiring override/hold could leave the battery
          stuck in real-time).
        dry_run / not_controlling / idempotency still apply to everyone; idempotency means even a
        bypassed write happens at most once per actual mode change (no device hammering).

        `force=True` skips ONLY the idempotency check (dry_run / not_controlling still apply). It
        exists for the car-charging discharge session (feat/car-charge-modes): the setpoint
        (power_w) can change while the physical mode stays DISCHARGE, and a mode-only idempotency
        check would otherwise swallow that re-command. The caller (control tick) sets it only when
        it has decided a bounded re-command is warranted (car_mode.recommand + a 10-min car dwell),
        so it never re-introduces device hammering.

        `commitment=True` marks a deadline-bearing committed grid-charge: it draws from the FULL
        daily cap, while a routine (commitment=False) switch is additionally bounded by
        (max_switches_per_day - commitment_reserve). This stops routine idle/auto flapping from
        exhausting the switches a committed charge relies on (the 07-12 starvation incident). See
        `_cap_block`. It changes ONLY the cap gate; dry_run / not_controlling / idempotency / dwell
        and every bypass (manual / priority / return-to-AUTO) are unaffected."""
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
        if desired == current and not force:
            return ActionDecision(intent, desired, False, "idempotent", f"already in {desired}")
        if manual or priority or desired is PhysicalMode.AUTO:
            return None  # operator command / safety hold / return-to-safe: never gated
        if self.last_switch_at is not None and now - self.last_switch_at < self.min_dwell:
            return ActionDecision(intent, desired, False, "dwell", "min dwell not elapsed; holding")
        return self._cap_block(intent, desired, now, commitment=commitment)

    def preview(
        self, intent: BatteryIntent, now: datetime, *,
        target_soc: float | None = None, power_w: float | None = None,
        observed_mode: PhysicalMode | None = None, manual: bool = False,
        priority: bool = False, car_session: bool = False, commitment: bool = False,
    ) -> ActionDecision:
        """Read-only: what decide() WOULD do right now. Never writes or mutates state. Pass
        `observed_mode` (a recently-observed mode) to avoid a hardware mode-read per call;
        `manual` / `priority` so the preview matches decide()'s gate bypass (see _gate);
        `car_session` so the previewed mode matches the car-charging discharge mapping (DISCHARGE
        rather than AUTO), keeping the dashboard/audit honest about what would run; `commitment` so
        the previewed cap outcome matches decide()'s routine-vs-commitment budget."""
        desired = self._desired(intent, car_session=car_session)
        blocked = self._gate(intent, now, desired, observed_mode=observed_mode, manual=manual,
                             priority=priority, commitment=commitment)
        if blocked is not None:
            return blocked
        suffix = (f" to {target_soc:.0f}%"
                  if target_soc is not None
                  and desired in (PhysicalMode.CHARGE, PhysicalMode.DISCHARGE) else "")
        return ActionDecision(intent, desired, False, "would_apply", f"would set {desired}{suffix}")

    def decide(
        self, intent: BatteryIntent, now: datetime, *,
        target_soc: float | None = None, power_w: float | None = None,
        observed_mode: PhysicalMode | None = None, manual: bool = False,
        priority: bool = False, car_session: bool = False, force: bool = False,
        count_toward_cap: bool = True, commitment: bool = False,
    ) -> ActionDecision:
        """Write path: applies at most one mode change. The ONLY caller of driver.apply. The plan's
        target SoC + power are passed through to the driver (which refuses a target-less charge).
        `observed_mode` (a recently-observed mode, e.g. from the shared coalesced cluster read) is
        used for the idempotency gate so the control loop needn't read the device every cycle; the
        post-write CONFIRM still re-reads fresh, so a stale observation can at worst cause one
        redundant (idempotent) write, never an unconfirmed change. `manual=True` (operator override)
        and `priority=True` (a safety action, e.g. the car-guard hold) bypass dwell + cap — see
        _gate. `car_session=True` maps DISCHARGE_FOR_LOAD to a real DISCHARGE at the bounded
        setpoint (feat/car-charge-modes); `force=True` lets a car-session setpoint re-command past
        the mode-only idempotency gate (also see _gate).

        `count_toward_cap=False` records a write WITHOUT advancing the daily switch counter or the
        dwell timer (F4). A car-session command is a priority write whose cadence is bounded by the
        car session's OWN gates (its 10-min dwell / reconciliation spacing / 6-command cap); making
        it also spend the planner's daily switch budget starved the ordinary planner of switches for
        the rest of the day. Ordinary planner writes leave it True and are unchanged.

        `commitment=True` marks a committed grid-charge so it draws from the full daily cap and its
        write is accounted against the commitment budget (see _cap_block); routine planner writes
        leave it False and are bounded by (max_switches_per_day - commitment_reserve)."""
        desired = self._desired(intent, car_session=car_session)
        blocked = self._gate(intent, now, desired, observed_mode=observed_mode, manual=manual,
                             priority=priority, force=force, commitment=commitment)
        if blocked is not None:
            return blocked

        def _record_switch() -> None:
            # A write hit the device: start the dwell timer + spend a daily switch — UNLESS this is
            # a car-session command (count_toward_cap=False), whose cadence is bounded by the car
            # session's own gates and must not starve the planner's budget (F4). A committed
            # grid-charge also advances the commitment sub-count, so the routine budget it drew from
            # is tracked separately (07-12 starvation fix).
            if count_toward_cap:
                self.switches_today += 1
                if commitment:
                    self.commitment_switches_today += 1
                self.last_switch_at = now

        self._reset_counter_if_new_day(now)
        # Capture the mode the battery was in BEFORE EMS first took control (to hand it back later).
        if self.original_vendor_mode is None:
            try:
                self.original_vendor_mode = self.driver.current_mode()
            except Exception:
                pass
        self.last_requested_action = desired
        try:
            accepted = self.driver.apply(desired, target_soc=target_soc, power_w=power_w)
        except BatteryWriteUnconfirmed as exc:
            # The write TIMED OUT (device slow/unreachable) — NOT a rejection. Do NOT revert: the
            # AUTO revert would also time out, leaving a half-known cluster and an ALERT spiral (the
            # live failure mode). The device very likely received the command (it switches with
            # latency); hold the intent and let the NEXT cycle read the real mode and re-command.
            # Count it + start the dwell timer so automatic retries are spaced (a manual override
            # bypasses dwell and may retry sooner — what the operator wants).
            _record_switch()
            # F3: audit the FIRST unconfirmed of a stuck episode (and re-log after ~60 min);
            # suppress the duplicate rows in between. HOLD/retry behaviour above is untouched — only
            # the audit noise: one "charge isn't sticking because the device is slow" is one row.
            audit = self._note_unconfirmed_episode(intent, desired, now)
            self._persist()
            return ActionDecision(intent, desired, False, "unconfirmed",
                                  f"{desired} unconfirmed — device slow/unreachable ({exc}); "
                                  "holding (not reverting), will re-verify next cycle", audit=audit)
        if not accepted:
            # Genuine device REJECTION (result:false) -> revert to the safe vendor mode (SPEC §6.5).
            try:
                recovered = self.driver.apply(PhysicalMode.AUTO)
            except BatteryWriteUnconfirmed:
                recovered = False  # revert write also timed out
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
            _record_switch()
            self._clear_unconfirmed_episode()  # F3: a rejection is a distinct terminal event
            self._persist()
            return ActionDecision(intent, PhysicalMode.AUTO, recovered, outcome, reason)
        _record_switch()
        self.last_confirmed_action = desired
        self._clear_unconfirmed_episode()  # F3: a confirmed write ends the episode → re-audit later
        self._persist()
        return ActionDecision(intent, desired, True, "applied", f"set {desired}")

    def _reset_counter_if_new_day(self, now: datetime) -> None:
        today = now.astimezone(self.tz).date()
        if self._counter_date != today:
            self.switches_today = 0
            self.commitment_switches_today = 0  # the split budget rolls over with the total cap
            self._counter_date = today
