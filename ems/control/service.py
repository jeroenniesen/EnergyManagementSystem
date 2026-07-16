"""Control service + control-cycle state (BACKLOG B-46, stage 1).

The EMS "brain" — the periodic sense→plan→decide→act→confirm loop (SPEC §13 `cycle()`) — used to
live entirely as nested closures inside `create_app`, sharing ~10 mutable `*_box` dicts and reaching
19 collaborators through closure capture. B-46 lifts the *seam* out: the mutable state becomes a
typed `ControlContext`, and the decision/plan/act logic becomes `ControlService` methods that take
their collaborators explicitly via the constructor. The result is a unit that can be constructed and
ticked with mocks, outside FastAPI (see `ems/tests/test_control_service.py`).

STAGE 1 is deliberately conservative — it moves the *control cycle and its state*, not the whole
organ. The coalesced live reads (`_current_sample`/`_current_towers`/…), the data-quality /
freshness helpers, the strategy/hysteresis resolution and the planner-config builders stay as
closures in `api.py` and are injected here as plain callables (a clean, mockable seam); the
endpoints keep calling those same closures unchanged. `api.py` constructs one `ControlService`
and delegates the control loop / override-triggered cycle to it, keeping thin aliases so every
existing route and every closure-testing test keeps working.

SETTINGS: `settings` is THE live effective-settings dict — mutated in place by the lifespan and by
POST /api/settings (never rebound). The service holds the reference (never a copy), exactly like
`AppContext.settings_cache`, so a just-saved value takes effect on the very next cycle.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ems.control.car_mode import (
    CarModeAction,
    decide_car_mode_action,
    predict_house_load_w,
)
from ems.control.failsafe import failsafe_intent
from ems.control.override import NONE as OVERRIDE_NONE
from ems.control.override import Override
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import OwnershipState
from ems.planner.recovery import recover_if_needed
from ems.planner.strategy import HysteresisState, build_plan
from ems.planner.validator import PlanValidation

_log = logging.getLogger("ems.recorder")


# --- Cluster per-tower mode mapping (moved verbatim from api.py; re-exported there) --------------
# Cluster per-tower mode LABEL (from tower_mode_label) → PhysicalMode, so the coalesced cluster
# read can serve the control loop's idempotency/reachability without a separate master mode-read.
_LABEL_TO_MODE = {
    "self-consumption": PhysicalMode.AUTO,
    "standby": PhysicalMode.IDLE,
    "charging": PhysicalMode.CHARGE,
    "discharging": PhysicalMode.DISCHARGE,
    "outdoor": PhysicalMode.AUTO,
    "schedule": PhysicalMode.AUTO,
}
# Mode FAMILY for cluster-consistency checks: the STABLE distinction is vendor self-consumption
# (mode 1) vs EMS real-time control (mode 4). charging/discharging/standby are transient real-time
# states (a battery that finished charging shows "standby", not "charging") — so we compare at the
# family level to avoid false "didn't follow" flags. outdoor/schedule/unknown → None (don't judge).
_REALTIME_LABELS = {"standby", "charging", "discharging"}


def _tower_family(label: str | None) -> str | None:
    if label == "self-consumption":
        return "self-consumption"
    if label in _REALTIME_LABELS:
        return "real-time"
    return None


def _commanded_family(mode: PhysicalMode) -> str:
    return "self-consumption" if mode is PhysicalMode.AUTO else "real-time"


# --- Car-charging discharge session constants + pure decisions (moved verbatim from api.py) -------
# How far back to pull observation rows for the non-EV house-load prediction (same-weekday-hour
# matched inside predict_house_load_w). Four weeks gives a few samples per (weekday, hour) bucket;
# a bounded, cheap query cached per control cycle. Horizon: the next ~2h the car keeps charging.
_CAR_PRED_LOOKBACK_DAYS = 28
_CAR_PRED_HORIZON_HOURS = 2.0
# A SEPARATE, deliberately conservative safety dwell between car-mode battery commands (independent
# of the planner's own min_dwell/cap, which a car-session command bypasses as a priority action).
# It bounds how often a moving prediction can re-command the setpoint, on top of car_mode's own
# rebond threshold.
_CAR_SESSION_DWELL = timedelta(minutes=10)
# Hard belt-and-braces ceiling on commands per session — beyond this we hold the last setpoint and
# warn, so a pathologically oscillating prediction can never hammer the battery.
_CAR_SESSION_MAX_COMMANDS = 6
_CAR_ATTEMPTED_OUTCOMES = frozenset(
    {"applied", "unconfirmed", "failed_recovered", "failed_unrecovered"})


def _decide_car_command(
    session: dict, car_action: CarModeAction, now: datetime, *,
    dwell: timedelta = _CAR_SESSION_DWELL, max_commands: int = _CAR_SESSION_MAX_COMMANDS,
) -> tuple[bool, dict, str]:
    """PURE. Given the current car-session box, the discharge `CarModeAction` decided this cycle and
    `now`, decide whether to actually (re-)command the battery and compute the NEXT session box.

    Returns ``(command, next_session, event)`` where `event` is one of ``"start"`` (first command of
    a session), ``"recommand"`` (a later command), ``"hold"`` (keep the current setpoint, no write)
    or ``"cap"`` (a re-command was wanted but the per-session command budget is spent → hold+warn).

    Layered on top of `car_action.recommand` (car_mode's own bounded re-command rule) are two extra
    safety gates that live in the wiring, not the pure core: a >= `dwell` gap between car-mode
    commands, and a hard `max_commands` ceiling per session. This is what keeps a whole charging
    session to a handful of writes even with a noisy prediction — assert-tested directly."""
    active = bool(session.get("active"))
    setpoint = session.get("setpoint_w")
    commands = int(session.get("commands") or 0)
    commanded_at = session.get("commanded_at")
    first = not active
    dwell_ok = True
    if commanded_at:
        try:
            dwell_ok = (now - datetime.fromisoformat(commanded_at)) >= dwell
        except (TypeError, ValueError):
            dwell_ok = True  # a corrupt timestamp must never wedge the session
    want = car_action.recommand and (first or dwell_ok)
    if want and commands >= max_commands:
        # Budget spent: stop chasing the prediction, hold the last setpoint (no write).
        return False, dict(session, active=True), "cap"
    if want:
        nxt = {"active": True, "setpoint_w": car_action.power_w,
               "commanded_at": now.isoformat(), "commands": commands + 1}
        return True, nxt, ("start" if first else "recommand")
    # Hold this cycle — keep the session alive at its current setpoint (first cycle seeds it).
    seed = setpoint if setpoint is not None else car_action.power_w
    return False, {"active": True, "setpoint_w": seed,
                   "commanded_at": commanded_at, "commands": commands}, "hold"


def _decide_car_session_end(
    session: dict, *, car_below_threshold: bool, end_cycles: int,
) -> tuple[bool, int]:
    """PURE. Session-END hysteresis (production audit: a Tesla's charging power briefly dips below
    `control.car_charging_threshold_w` — three-phase balancing / ramp pauses — so an active session
    was ending and immediately restarting, each flip issuing a battery mode command).

    Given the current session box and whether THIS cycle read below the threshold, decide whether
    the session should actually end now. Only a below-threshold READ (`car_below_threshold=True`)
    counts toward the grace window: `end_cycles` (>= 1) consecutive below-threshold cycles are
    required before ending, and the counter resets the moment a cycle reads above threshold again.
    `car_below_threshold=False` (this cycle's car reading is still above the threshold, but the
    session is ending for some OTHER reason — the reserve floor reached, the master switch toggled
    off) always ends the session on the spot, counter reset to 0 — those are genuine state changes,
    never power-reading noise, and must never be delayed (safety).

    Returns ``(end_now, next_below_threshold_cycles)``; the caller resets the whole box when
    `end_now`, else merges the counter back into the session box. Session START is untouched —
    it stays immediate, handled entirely by `_decide_car_command` above."""
    if not car_below_threshold:
        return True, 0
    cycles = int(session.get("below_threshold_cycles") or 0) + 1
    if cycles < max(1, end_cycles):
        return False, cycles
    return True, 0


@dataclass
class ControlContext:
    """The mutable state the control cycle OWNS, as explicit typed fields instead of ~10 free
    `*_box` dicts captured by closure in `create_app`. Every field is the SAME shared object the
    service and (for a few) the read endpoints mutate in place — always pass the reference, never a
    copy (the `settings_cache` convention).

    The dicts keep their single-key `{"...": ...}` box shape deliberately: it lets a closure or a
    method mutate the value through a stable reference (rebinding a bare attribute wouldn't be seen
    by another holder). Initial values match the old `create_app` locals byte-for-byte."""

    # Current manual override (expiry evaluated per request); loaded in lifespan, set by POST
    # /override. Read by the read endpoints AND the control service's effective-intent.
    override_box: dict[str, Override] = field(default_factory=lambda: {"ov": OVERRIDE_NONE})
    # Seasonal-transition hysteresis memory (SPEC §8.4 / B-15). Seeded from the KV cache at boot,
    # persisted only when it CHANGES. Owned by api.py's strategy resolution; lives here so it is one
    # explicit piece of control state.
    hysteresis_box: dict[str, HysteresisState] = field(
        default_factory=lambda: {"state": HysteresisState()})
    # Serialise control cycles: the periodic loop and an immediate override-triggered cycle must not
    # run decide()/apply() concurrently (two writes racing the battery).
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Short in-memory coalescing of the (slow) live meter/SoC read + the per-tower cluster read, so
    # one dashboard refresh that fans out to several endpoints reads the hardware once. The
    # single-flight locks mean exactly ONE thread reads per window at cache expiry (flood safety).
    sample_cache: dict[str, Any] = field(default_factory=lambda: {"sample": None, "at": None})
    tower_cache: dict[str, Any] = field(default_factory=lambda: {"towers": None, "at": None})
    sample_lock: threading.Lock = field(default_factory=threading.Lock)
    tower_lock: threading.Lock = field(default_factory=threading.Lock)
    # Cluster-drift dedup: the signature of towers currently NOT in the commanded mode family, so a
    # mismatch (or its resolution) is audited ONCE per episode. Also read by /api/diagnostics.
    drift_box: dict[str, Any] = field(default_factory=lambda: {"sig": None})
    # Held-decision dedup: the (outcome, desired_mode) last audited as a HELD/blocked decision
    # (dwell/cap/not_controlling/unconfirmed), so a recurring hold is explained ONCE, not per cycle.
    held_box: dict[str, Any] = field(default_factory=lambda: {"sig": None})
    # Car-charging discharge session (feat/car-charge-modes). IN-MEMORY ONLY: a restart mid-session
    # simply starts a fresh session and re-commands ONCE next cycle (documented, acceptable). Keys:
    # {active, setpoint_w (last commanded W), commanded_at (iso of last car command), commands,
    # below_threshold_cycles (the end-hysteresis counter, see car_session_end_if_active)}.
    car_session: dict[str, Any] = field(default_factory=lambda: {
        "active": False, "setpoint_w": None, "commanded_at": None, "commands": 0,
        "below_threshold_cycles": 0})
    # Per-control-cycle cache of recent observation rows for the non-EV house-load prediction,
    # refreshed by the async control cycle ONLY while the car is charging (a bounded, cheap query).
    car_obs_box: dict[str, Any] = field(default_factory=lambda: {"rows": None, "at": None})
    # Strong refs to fire-and-forget override-triggered control cycles (_spawn_tracked). Without
    # this the loop keeps only a weak ref and the task can be GC'd mid-run (the "charge now" no-op).
    override_tasks: set[asyncio.Task] = field(default_factory=set)
    # Cached expected-load profile (learned async in _forward_projection) so the sync plan path can
    # feed the adaptive charger without its own DB read. None until the first projection runs.
    load_profile_box: dict[str, Any] = field(default_factory=lambda: {"profile": None})


class ControlService:
    """Owns the control cycle: the plan-to-act path, the intent resolution, the car-charging session
    lifecycle, and the single per-cycle battery write. Collaborators are injected explicitly so the
    service is testable outside FastAPI. The coalesced live reads, data-quality, strategy resolution
    and planner-config builders stay in `api.py` and are passed here as callables (`current_soc`,
    `data_quality`, `active_strategy`, `planner_cfg`, …) — the seam B-46 stage 2 pushes further."""

    def __init__(
        self,
        *,
        ctx: ControlContext,
        settings: dict[str, Any],
        controller: Any | None,
        store: Any | None,
        audit_store: Any | None,
        price_source: Any | None,
        solar_forecast: Any | None,
        site_tz: Any,
        dry_run: bool,
        # --- injected callables (closures that stay in api.py) ---------------------------------
        current_soc: Callable[[datetime], float],
        current_mode: Callable[[datetime], PhysicalMode | None],
        current_towers: Callable[[datetime], Any],
        data_quality: Callable[[datetime], str],
        car_charging: Callable[[datetime], bool],
        load_by: Callable[[list[datetime]], dict[datetime, float]],
        active_strategy: Callable[[datetime], str],
        validate_plan_obj: Callable[[Any, datetime], PlanValidation],
        planner_cfg: Callable[[], Any],
        summer_cfg: Callable[[float], Any],
        adaptive_cfg: Callable[[], Any],
    ) -> None:
        self._ctx = ctx
        self._settings = settings  # the live shared dict — never copied (see module docstring)
        self._controller = controller
        self._store = store
        self._audit_store = audit_store
        self._price_source = price_source
        self._solar_forecast = solar_forecast
        self._site_tz = site_tz
        self._dry_run = dry_run
        self._current_soc = current_soc
        self._current_mode = current_mode
        self._current_towers = current_towers
        self._data_quality = data_quality
        self._car_charging = car_charging
        self._load_by = load_by
        self._active_strategy = active_strategy
        self._validate_plan_obj = validate_plan_obj
        self._planner_cfg = planner_cfg
        self._summer_cfg = summer_cfg
        self._adaptive_cfg = adaptive_cfg

    # --- recovery-integrated plan path -----------------------------------------------------------
    def recovery_sizing(self) -> dict:
        """Battery sizing the §8.12 catch-up needs, from the live settings cache."""
        s = self._settings
        return {
            "usable_kwh": s["battery.usable_kwh"],
            "reserve_soc_pct": s["battery.min_reserve_soc"],
            "max_charge_w": s["battery.max_charge_w"],
            "round_trip_efficiency": s["planner.round_trip_efficiency"],
        }

    def build_plan_now(self):
        """The fresh plan the active strategy builds THIS instant, BEFORE any missed-window
        recovery. Dispatches to the active strategy (summer solar-first / winter arbitrage).
        Returns (now, prices, plan) or None. Used by `plan_with_recovery` and the recovery cycle."""
        if self._price_source is None:
            return None
        now = datetime.now(UTC)
        prices = self._price_source.slots()
        strategy = self._active_strategy(now)
        # BOTH seasons now use the adaptive (demand-aware) charger, so both need the live SoC, the
        # solar forecast and the expected-load profile — winter sizes the top-up to the evening
        # peak load above reserve, not just the cheapest slots (energy review P1.2).
        soc = self._current_soc(now)
        forecast = self._solar_forecast.slots() if self._solar_forecast is not None else []
        load_by = self._load_by([p.start for p in prices])
        plan = build_plan(
            strategy, prices=prices, forecast=forecast, now=now, soc_pct=soc,
            winter_cfg=self._planner_cfg(), summer_cfg=self._summer_cfg(soc),
            load_w_by=load_by, adaptive_cfg=self._adaptive_cfg(),
        )
        return now, prices, plan

    def plan_with_recovery(self):
        """Single source of the plan to ACT on (DRY) so /api/plan, /api/savings, /api/decision, the
        control loop and the validator all reflect the SAME computation: the fresh strategy plan
        with SPEC §8.12 missed-window recovery folded in (BACKLOG B-16). Recovery is a PURE,
        deterministic reshape — when a committed grid-charge window is missed and the deadline is
        still ahead, it re-routes the charge to the cheapest REMAINING slots toward the SAME target;
        otherwise it returns the plan untouched. Because it runs here, the recovered plan still
        passes through `validate_plan_obj` (§8.11 incl. the B-22 projection gate) and the control
        caps/dwell before any write — recovery bypasses nothing. Returns (now, prices, plan)."""
        pp = self.build_plan_now()
        if pp is None:
            return None
        now, prices, plan = pp
        recovered, status, catch = recover_if_needed(
            plan, now, soc_pct=self._current_soc(now), prices=prices,
            enabled=bool(self._settings["planner.recovery_enabled"]), **self.recovery_sizing(),
        )
        return now, prices, recovered, status, catch

    def current_plan(self):
        pp = self.plan_with_recovery()
        return None if pp is None else pp[:3]

    # --- car-charging guard ----------------------------------------------------------------------
    def _car_predicted_house_w(self, now: datetime) -> float:
        """Predicted non-EV house load (W) for the car-mode discharge setpoint: same-weekday-hour
        observations (cached per cycle in `ctx.car_obs_box`) with the learned load profile as the
        fallback (or the overnight-load baseline if no profile yet). Pure wrt the caller — never
        reads a clock or the device."""
        rows = self._ctx.car_obs_box.get("rows") or []
        prof = self._ctx.load_profile_box["profile"]
        profile_w = (prof.expected_w(now) if prof is not None
                     else self._settings["battery.overnight_load_kwh"] * 1000.0 / 12.0)
        return predict_house_load_w(rows, profile_w, now=now,
                                    horizon_hours=_CAR_PRED_HORIZON_HOURS)

    def _car_mode_action(self, now: datetime, *, current_setpoint_w: float | None = None):
        """Resolve the car-charging battery action this cycle, or None when car-mode is dormant
        (master switch off, or the car isn't drawing above the threshold). Reads the mode + wattage
        from settings, SoC from the coalesced sample (NEVER a fresh device read — flood risk) and
        the predicted house load. Delegates the actual decision to the pure `decide_car_mode_action`
        and does NOT mutate the session box."""
        if (not self._settings["control.hold_battery_when_car_charging"]
                or not self._car_charging(now)):
            return None
        return decide_car_mode_action(
            self._settings["control.car_charging_battery_mode"],
            car_charging=True, soc_pct=self._current_soc(now),
            min_reserve_soc=self._settings["battery.min_reserve_soc"],
            max_discharge_w=self._settings["battery.max_discharge_w"],
            static_w=self._settings["control.car_discharge_w"],
            predicted_house_w=self._car_predicted_house_w(now),
            current_setpoint_w=current_setpoint_w,
        )

    def _car_guard(self, now: datetime, intent, reason):
        """Never let the home battery FEED the car — the final guardrail over the plan AND a manual
        override. While the car is charging (and the master switch is on) it consults the operator's
        chosen behaviour via `decide_car_mode_action`:

        * ``hold``            → force HOLD_RESERVE (today's behaviour, byte-for-byte): the battery
                                idles so it can't discharge into the car; solar + grid cover it.
        * ``static_discharge``/``match_home_load`` → force DISCHARGE_FOR_LOAD at the decided,
                                bounded setpoint (carried out via `car_action.power_w` below) so the
                                battery covers the (predicted) HOUSE while the grid feeds the car.

        Returns ``(intent, reason, car_action)``; `car_action` is the CarModeAction (or None when
        car-mode is dormant / the plan intent isn't discharge-shaped). GRID_CHARGE/HOLD plan intents
        pass through untouched, exactly as before. FAIL-SAFE: a discharge is suppressed to a HOLD
        when data quality is `unsafe` — an untrusted SoC must never drive a discharge (CLAUDE)."""
        if intent is None or intent not in (
                BatteryIntent.DISCHARGE_FOR_LOAD, BatteryIntent.ALLOW_SELF_CONSUMPTION):
            return intent, reason, None
        car_action = self._car_mode_action(
            now, current_setpoint_w=self._ctx.car_session["setpoint_w"])
        if car_action is None:
            return intent, reason, None
        if car_action.action == "discharge":
            if self._data_quality(now) == "unsafe":
                return (BatteryIntent.HOLD_RESERVE,
                        "car charging — holding the battery so it won't discharge into the car "
                        "(sensor data is unsafe, so EMS won't discharge on an untrusted level)",
                        None)
            return BatteryIntent.DISCHARGE_FOR_LOAD, car_action.reason, car_action
        if car_action.action == "hold":
            return BatteryIntent.HOLD_RESERVE, car_action.reason, None
        return intent, reason, None  # "none" — defensive (car_charging was True), unchanged

    # --- effective intent ------------------------------------------------------------------------
    def effective_intent(self, now: datetime):
        """The intent the controller should act on now + its energy sizing, honouring an active
        manual override and the data-quality fail-safe. Returns (intent|None, reason|None,
        override_active, target_soc|None, power_w|None, validation|None, car_action|None).

        An active override wins over the plan AND the fail-safe (deliberate, time-boxed operator
        action — the UI shows the data-quality badge). The planner path is gated: unsafe data falls
        back to self-consumption (CLAUDE.md "fail safe"). Sizing (target_soc/power_w) is taken from
        the SAME plan slot we resolved, and ONLY when the final intent still matches that slot's
        intent — an override or a fail-safe substitution carries no sizing, so a stale target can
        never leak to the driver. A target is emitted only for a physical CHARGE (the slot target
        SoC) or, when export-discharge is enabled, a forced DISCHARGE (the reserve floor); a
        DISCHARGE_FOR_LOAD that maps to AUTO needs none.

        `car_action` (the 7th element) is the CarModeAction chosen by the car-charging guard, or
        None when car-mode is dormant. When it is a discharge, the intent is DISCHARGE_FOR_LOAD at
        the bounded car setpoint (power_w = car_action.power_w, target_soc = the reserve floor) and
        the control tick treats it as a car session (car_session=True → a real DISCHARGE)."""
        cur = None
        val: PlanValidation | None = None
        ov = self._ctx.override_box["ov"]
        if ov.active(now):
            assert ov.intent is not None and ov.expires_at is not None
            until = ov.expires_at.astimezone(self._site_tz).strftime("%H:%M")
            intent, override_active = ov.intent, True
            # Gate a RISKY override (anything other than self-consumption) on data quality: EMS
            # won't force charge/discharge/hold when critical data is unsafe — it can't trust SoC or
            # reachability. Returning to self-consumption is always allowed (energy review #5).
            risky = intent is not BatteryIntent.ALLOW_SELF_CONSUMPTION
            if risky and self._data_quality(now) == "unsafe":
                intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
                reason = (f"manual override held — sensor data is unsafe, so EMS won't force "
                          f"{ov.intent.value}; holding self-consumption until {until}")
            else:
                reason = f"manual override: {ov.intent.value} until {until}"
        else:
            pp = self.current_plan()
            if pp is None:
                return None, None, False, None, None, None, None
            cur = pp[2].intent_at(now)
            if cur is None:
                return None, None, False, None, None, None, None
            # §8.11 hard gate: a plan that fails validation (impossible target, projected below
            # reserve, …) must not be acted on — hold self-consumption, like the data fail-safe.
            # Validate the plan we ALREADY fetched (no second current_plan rebuild).
            val = self._validate_plan_obj(pp[2], now)
            if not val.ok:
                top = next((f for f in val.findings if f.severity == "unsafe"), None)
                note = top.message if top is not None else "plan failed validation"
                cur = None  # not acting on a plan slot — sizing must be None below
                intent, reason, override_active = (
                    BatteryIntent.ALLOW_SELF_CONSUMPTION,
                    f"holding self-consumption — {note}", False)
            else:
                safe, fs_reason = failsafe_intent(cur.intent, self._data_quality(now))
                intent, reason = ((safe, fs_reason) if fs_reason is not None
                                  else (cur.intent, cur.reason))
                override_active = False
        # Final guardrail (over the plan AND a manual override): never FEED the car — hold, or (if
        # the operator chose a discharge behaviour) cover the house at a bounded setpoint.
        intent, reason, car_action = self._car_guard(now, intent, reason)
        target_soc = power_w = None
        if car_action is not None and car_action.action == "discharge":
            # Car session: the setpoint is authoritative; the reserve floor is the stop. This takes
            # precedence over the override/plan sizing below (it IS the final guardrail).
            power_w = car_action.power_w
            target_soc = self._settings["battery.min_reserve_soc"]
        elif override_active:
            # A manual override is an EXPLICIT operator command, so it carries its own target —
            # "charge now" means charge toward full (deliberate, not the planner's silent default),
            # a forced discharge stops at the reserve floor. (Gated overrides held to
            # self-consumption fall through with no target, which is correct.)
            if intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
                target_soc = 100.0
                # "charge now" = charge at the configured cluster max (default 4 kW), which the
                # driver then splits across towers — not the driver's conservative 2 kW default.
                power_w = self._settings["battery.max_charge_w"]
            elif (intent is BatteryIntent.DISCHARGE_FOR_LOAD and self._controller is not None
                  and self._controller.allow_export_discharge):
                target_soc = self._settings["battery.min_reserve_soc"]
                power_w = self._settings["battery.max_discharge_w"]
        elif cur is not None and intent is cur.intent:
            if intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
                target_soc, power_w = cur.target_soc, cur.power_w
            elif (intent is BatteryIntent.DISCHARGE_FOR_LOAD and self._controller is not None
                  and self._controller.allow_export_discharge):
                target_soc, power_w = cur.floor_soc, cur.power_w  # forced discharge → reserve floor
        return intent, reason, override_active, target_soc, power_w, val, car_action

    # --- cluster-drift audit ---------------------------------------------------------------------
    def cluster_drift_record(self, desired: PhysicalMode, towers) -> dict | None:
        """Deduped audit record when the cluster doesn't match the commanded mode FAMILY. Returns a
        record once when drift starts and once when it clears; None while unchanged. towers = the
        coalesced per-tower read (steady-state).

        CLUSTER MODEL (asymmetric, verified live): the EMS commands the MASTER for real-time, which
        drives the slaves in lockstep — a following slave keeps REPORTING self-consumption (7101=1)
        even while it charges. So:
        - Commanded REAL-TIME (charge/discharge/idle): judge the MASTER only (it reflects the mode);
          a slave in self-consumption is FOLLOWING, not a fault — judging it falsely flagged it.
        - Commanded SELF-CONSUMPTION (AUTO): every tower is commanded and must return to it, so flag
          ANY tower still stuck in real-time (the genuine "didn't revert" fault)."""
        if not towers:
            return None
        want = _commanded_family(desired)
        if want == "self-consumption":
            judged = [t for t in towers if t.online]  # all must have returned to self-consumption
        else:
            masters = [t for t in towers if t.online and t.role == "master"]
            judged = masters or [t for t in towers if t.online]  # master drives real-time
        laggards = [t for t in judged
                    if t.mode and _tower_family(t.mode) not in (want, None)]
        sig = tuple(sorted((t.ip, t.mode) for t in laggards))
        if sig == self._ctx.drift_box["sig"]:
            return None  # already reported this exact state
        prev = self._ctx.drift_box["sig"]
        self._ctx.drift_box["sig"] = sig
        if not laggards:
            return ({"summary": "Battery cluster back in sync — all towers match the commanded "
                                "mode",
                     "detail": {"event": "drift_resolved", "commanded": desired.value}}
                    if prev else None)
        modes = {t.ip: t.mode for t in laggards}
        return {
            "summary": (f"Battery cluster MISMATCH — {len(laggards)} tower(s) NOT following the "
                        f"commanded {desired.value}: {', '.join(sorted(set(modes.values())))}"),
            "detail": {"event": "cluster_drift", "commanded": desired.value, "laggards": modes},
        }

    # --- car-charging session lifecycle ----------------------------------------------------------
    def car_session_reset(self) -> None:
        self._ctx.car_session.update(active=False, setpoint_w=None, commanded_at=None, commands=0,
                                     below_threshold_cycles=0)

    def car_session_end_if_active(self, now: datetime) -> list[dict] | None:
        """If a car discharge session is active but this cycle is no longer a car discharge (car
        dipped below threshold, mode switched to hold, master toggled off, or the reserve floor
        reached), decide whether to actually end it now.

        A dip below `control.car_charging_threshold_w` (the production flap: three-phase balancing
        / charging ramp pauses briefly drop EV power) is given `control.car_session_end_cycles`
        consecutive cycles of grace (`_decide_car_session_end`) before the session ends — so a
        one-cycle blip no longer flaps the battery mode on and off. Any OTHER reason (reserve
        floor, master switch off) ends the session immediately, exactly as before this fix.

        Returns ``None`` while the grace window is still open — the caller must hold this cycle
        (no ordinary decide() call, no write) rather than resume the plan early. Otherwise returns
        a (possibly empty) list of audit records: non-empty only when a session just ended (a
        hysteresis-deferred end is NOT audited — silence is the point; see the debug log below).
        The NORMAL intent (the resolved plan/hold) then flows through the ordinary decide() path
        below — mirroring how today's car-guard hold simply releases and lets the next intent
        apply (return-to-AUTO / plan intent is never gated)."""
        if not self._ctx.car_session["active"]:
            return []
        end_cycles = int(self._settings["control.car_session_end_cycles"])
        ended, cycles = _decide_car_session_end(
            self._ctx.car_session, car_below_threshold=not self._car_charging(now),
            end_cycles=end_cycles)
        if not ended:
            self._ctx.car_session["below_threshold_cycles"] = cycles
            _log.debug("car session: below-threshold cycle %d/%d — holding session open "
                      "(no end yet)", cycles, end_cycles)
            return None
        self.car_session_reset()
        return [{"summary": "car session ended — resuming plan",
                 "detail": {"event": "car_session_end"}}]

    def car_session_command(
        self, now: datetime, car_action: CarModeAction, target_soc, override_active, observed,
    ) -> list[dict]:
        """WRITE PATH: run one cycle of a car-charging discharge session. Consults the bounded
        re-command + 10-min car dwell + 6-command cap (`_decide_car_command`), commands the battery
        through the ONE writer (controller.decide, car_session=True so DISCHARGE_FOR_LOAD becomes a
        real DISCHARGE; force=True only on an actual (re-)command so a setpoint change within
        DISCHARGE isn't swallowed by mode-only idempotency), mutates the in-memory session box, and
        returns audit records. A transport timeout during a car command rides the established
        BatteryWriteUnconfirmed HOLD path (decide never raises past its contract; it returns the
        `unconfirmed` outcome — we hold, we do NOT revert)."""
        session = self._ctx.car_session
        command, nxt, event = _decide_car_command(session, car_action, now)
        power = car_action.power_w
        if not command:
            session.update(nxt)  # keep the session alive (hold the current setpoint, no write)
            if event == "cap":
                return [{"summary": ("car session: command budget spent — holding the current "
                                     f"setpoint (~{session['setpoint_w']:.0f} W), not "
                                     "re-commanding a moving prediction"),
                         "detail": {"event": "car_session_cap",
                                    "setpoint_w": session["setpoint_w"],
                                    "commands": session["commands"]}}]
            return []  # quiet hold — the normal steady state of a car session (no audit spam)
        dec = self._controller.decide(
            BatteryIntent.DISCHARGE_FOR_LOAD, now, target_soc=target_soc, power_w=power,
            observed_mode=observed, manual=override_active, priority=True,
            car_session=True, force=True)
        # Any attempted device write advances the session (bounds retries via the cap); dry-run /
        # not-controlling did not touch the device, so the session doesn't advance on those.
        if dec.outcome in _CAR_ATTEMPTED_OUTCOMES:
            session.update(nxt)
        else:
            # dry_run / not_controlling — no device write (unreachable here in prod: the tick gates
            # on lc.can_command and dry-run never runs the tick). Seed a coherent box anyway so a
            # later real command has a setpoint to compare against (no every-cycle recommand loop).
            session.update(active=True, setpoint_w=power)
        first = event == "start"
        lead = "car session started" if first else "car session"
        if dec.outcome == "applied":
            summary = f"{lead}: {car_action.reason} (command sent)"
        elif dec.outcome == "unconfirmed":
            summary = (f"{lead}: {power:.0f} W unconfirmed — device slow to respond; holding "
                       "(not reverting), will re-verify next cycle")
        elif dec.outcome in ("failed_recovered", "failed_unrecovered"):
            summary = f"{lead}: discharge command FAILED ({dec.reason})"
        elif dec.outcome == "dry_run":
            summary = f"{lead} (dry-run): would cover the house at {power:.0f} W — no write"
        else:  # not_controlling (or any future outcome) — surfaced honestly
            summary = f"{lead}: not commanded ({dec.reason})"
        return [{"summary": summary,
                 "detail": {"event": "car_session", "first": first, "setpoint_w": power,
                            "commands": session["commands"], "outcome": dec.outcome,
                            "accepted": dec.applied, "reason": car_action.reason,
                            "override_active": override_active}}]

    # --- the tick + the cycle --------------------------------------------------------------------
    def control_tick(self, now: datetime) -> list[dict]:
        """Operational mode ONLY: advance the ownership lifecycle and, once CONTROLLING, apply the
        current intent — the single battery write per cycle. Every safety gate (dwell, daily cap,
        fail-safe AUTO on unsafe data, override) is enforced by ModeController.decide /
        effective_intent. Returns audit records for the async caller to log: a CONFIRMED
        mode-change record when a write was attempted (applied/failed), and/or a cluster-mismatch
        record when a tower isn't following the commanded mode (steady state). [] = nothing."""
        if self._controller is None:
            return []
        lc = self._controller.lifecycle
        if lc.state is OwnershipState.INACTIVE:
            lc.start(now)
        # Readiness sequence (SPEC §13.3): validated sensors, a reachable battery, a loaded plan.
        if self._data_quality(now) != "unsafe":
            lc.mark_sensors_validated()
        # Reachability + idempotency reuse the SHARED coalesced cluster read (observed) instead of a
        # separate per-cycle master mode-read — far gentler on a device shared with HA + the app.
        # IMPORTANT: "reachable" = the battery RESPONDED this cycle (a tower online), NOT that its
        # mode decoded to a known label — else an unexpected mode value would stall ALL control
        # (incl. manual overrides). `observed` may be None; decide() then reads fresh.
        observed = self._current_mode(now)
        towers = self._current_towers(now)
        reachable = any(t.online for t in towers) if towers else observed is not None
        if reachable:
            lc.mark_probe_ok()  # battery readable this cycle
        if self.current_plan() is not None:
            lc.mark_plan_loaded()
        lc.tick(now)
        if not lc.can_command(now):
            return []
        intent, _reason, override_active, tgt, pw, _v, car_action = self.effective_intent(now)
        if intent is None:
            # End a dangling session, nothing else to do — [] both when already inactive and when
            # the end-hysteresis grace window is still open (nothing to act on either way).
            return self.car_session_end_if_active(now) or []
        # A car-charging discharge session owns its own bounded command cadence (a real DISCHARGE at
        # the covered-house setpoint) — handled separately from the ordinary single-write path.
        if car_action is not None and car_action.action == "discharge":
            self._ctx.car_session["below_threshold_cycles"] = 0  # car read above threshold this cyc
            return self.car_session_command(now, car_action, tgt, override_active, observed)
        # Not a car discharge this cycle: if a session was active (car dipped below threshold /
        # hold / mode-change) decide whether to end it now (see car_session_end_if_active) — a
        # below-threshold dip gets a few cycles' grace, so `None` means "still in the grace window,
        # hold — skip the ordinary decide() below" rather than resuming the plan on a blip.
        records = self.car_session_end_if_active(now)
        if records is None:
            return []
        # decide() uses `observed` for the idempotency gate; its post-write CONFIRM re-reads the
        # device fresh, so a stale observation only risks a redundant idempotent write. `manual` (an
        # active operator override) and `priority` (a SAFETY action — the car-guard hold while the
        # car charges) bypass the automatic dwell/cap gates: never leave the battery draining into
        # the car just because today's switch budget is spent (a return to AUTO is always allowed
        # too; see _gate).
        priority = self._car_charging(now)
        dec = self._controller.decide(intent, now, target_soc=tgt, power_w=pw,
                                      observed_mode=observed, manual=override_active,
                                      priority=priority)
        held = self._ctx.held_box
        if dec.outcome in ("applied", "failed_recovered", "failed_unrecovered"):
            # An ACTUAL device write — audit it. `accepted` = the device acknowledged the command
            # (result:true); the mode switches with latency, so whether it actually TOOK is verified
            # on a later cycle by the cluster-consistency check below (which flags a tower that
            # never follows). So this logs "command sent" / "FAILED", not a premature "confirmed".
            held["sig"] = None  # an action happened — re-explain any future hold afresh
            before = observed.value if observed is not None else "unknown"
            accepted = dec.applied
            records.append({
                "summary": (f"Battery mode {before} → {dec.desired_mode.value} — "
                            + ("command sent" if accepted else f"command FAILED ({dec.reason})")),
                "detail": {"from_mode": before, "desired_mode": dec.desired_mode.value,
                           "intent": str(dec.intent), "outcome": dec.outcome,
                           "accepted": accepted, "reason": dec.reason}})
        elif dec.outcome == "idempotent":
            # Steady state: EMS believes it's already in `desired`. VERIFY the whole cluster —
            # a tower that didn't follow (still self-consuming while we commanded real-time) is the
            # silent slave-not-following bug. Towers are fresh here (no write this cycle).
            held["sig"] = None
            drift = self.cluster_drift_record(dec.desired_mode, towers)
            if drift is not None:
                records.append(drift)
        elif dec.outcome == "unconfirmed":
            # The write TIMED OUT (device slow/unreachable) — we did NOT revert (the device likely
            # got it; reverting would also time out). Surface it (deduped) so a recurring "charge
            # isn't sticking because the battery is slow to answer" is visible, not silent.
            sig = (dec.outcome, dec.desired_mode.value)
            if held["sig"] != sig:
                held["sig"] = sig
                records.append({
                    "summary": (f"Battery {dec.desired_mode.value} unconfirmed — device slow to "
                                "respond; holding and retrying (not reverting)"),
                    "detail": {"desired_mode": dec.desired_mode.value, "intent": str(dec.intent),
                               "outcome": dec.outcome, "reason": dec.reason,
                               "override_active": override_active}})
        elif dec.outcome in ("dwell", "cap_reached", "not_controlling"):
            # A HELD decision: the EMS WANTED to switch but a guardrail blocked it. Never silent
            # (CLAUDE.md "explainability first — including why it is NOT acting"). Deduped so a
            # recurring hold is explained once per (outcome, desired_mode), not every cycle. With
            # the manual/return-to-AUTO bypass this only fires for the AUTOMATIC planner now.
            sig = (dec.outcome, dec.desired_mode.value)
            if held["sig"] != sig:
                held["sig"] = sig
                records.append({
                    "summary": f"Battery NOT switched to {dec.desired_mode.value} — {dec.reason}",
                    "detail": {"desired_mode": dec.desired_mode.value, "intent": str(dec.intent),
                               "outcome": dec.outcome, "reason": dec.reason,
                               "override_active": override_active}})
        return records

    async def refresh_car_obs(self, now: datetime) -> None:
        """Warm `ctx.car_obs_box` with a bounded recent slice of observations for the non-EV
        house-load prediction — ONLY while the car is charging (that's the only time the prediction
        is used, so the cheap query never runs otherwise). Best-effort: a failed read keeps the last
        good rows, and an empty box just falls back to the load profile inside
        `_car_predicted_house_w`."""
        if self._store is None or not self._car_charging(now):
            return
        start = (now - timedelta(days=_CAR_PRED_LOOKBACK_DAYS)).isoformat()
        try:
            self._ctx.car_obs_box["rows"] = await self._store.observations_between(
                start, now.isoformat())
            self._ctx.car_obs_box["at"] = now
        except Exception:
            _log.debug("car-mode observation read failed; keeping last good (non-fatal)",
                       exc_info=True)

    async def run_cycle(self) -> None:
        """One operational control cycle: run the (blocking) tick off the event loop, then AUDIT the
        CONFIRMED mode change it reports. Serialised by `ctx.control_lock` so the periodic loop and
        an immediate override-triggered run can't overlap (two concurrent writes to the battery)."""
        if self._controller is None or self._dry_run:
            return
        async with self._ctx.control_lock:
            now = datetime.now(UTC)
            await self.refresh_car_obs(now)  # warm the house-load prediction before the (sync) tick
            try:
                records = await asyncio.to_thread(self.control_tick, now)
            except Exception:
                _log.exception("control tick failed; retry next cycle (fail-safe)")
                return
            for rec in records:
                if self._audit_store is not None:
                    try:
                        await self._audit_store.append(now.isoformat(), "battery_decision",
                                                       rec["summary"], rec["detail"])
                    except Exception:
                        _log.warning("failed to write battery-decision audit", exc_info=True)
