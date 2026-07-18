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
    _RESERVE_ENTER_PP,
    CarModeAction,
    decide_car_mode_action,
    predict_house_load_w,
)
from ems.control.failsafe import failsafe_intent
from ems.control.override import NONE as OVERRIDE_NONE
from ems.control.override import Override
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import OwnershipState
from ems.perf import PERF_BUDGETS, REGISTRY, atimed, timed
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.charge_need import compute_charge_need
from ems.planner.recovery import recover_if_needed
from ems.planner.rule_based import PlannerConfig
from ems.planner.strategy import HysteresisState, build_plan, resolve_strategy_hysteretic
from ems.planner.summer import SummerConfig
from ems.planner.validator import PlanValidation
from ems.sources.battery import intent_to_mode

_log = logging.getLogger("ems.recorder")

# How long a live meter/SoC read is reused before the hardware is re-read (UI-tunable via
# control.live_read_seconds; this is the fallback). Moved from api.py with the coalesced reads.
_LIVE_SAMPLE_COALESCE_SECONDS = 30.0
# Seasonal-transition hysteresis persistence (§8.4 / B-15). The KEY is also read by the api.py
# lifespan to SEED the box at boot, so it's exported here as the single source of truth.
HYSTERESIS_KEY = "strategy:hysteresis"
HYSTERESIS_TTL_SECONDS = 90 * 24 * 3600.0  # long enough to bridge shoulder-season gaps


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
# Outcomes that mean the last car command may NOT have actually taken (F1 reconciliation): a
# timed-out write, or a rejection — the battery might still be draining into the car, so re-command.
_CAR_BAD_OUTCOMES = frozenset({"unconfirmed", "failed_recovered", "failed_unrecovered"})


def _decide_car_command(
    session: dict, car_action: CarModeAction, now: datetime, *,
    observed_mode: PhysicalMode | None = None,
    dwell: timedelta = _CAR_SESSION_DWELL, max_commands: int = _CAR_SESSION_MAX_COMMANDS,
    reconcile_spacing: timedelta = _CAR_SESSION_DWELL,
) -> tuple[bool, dict, str]:
    """PURE. Given the current car-session box, the discharge `CarModeAction` decided this cycle and
    `now`, decide whether to actually (re-)command the battery and compute the NEXT session box.

    Returns ``(command, next_session, event)`` where `event` is one of ``"start"`` (first command of
    a session), ``"recommand"`` (a later prediction-driven command), ``"reconcile"`` (a safety
    re-command because the battery isn't actually discharging — see F1), ``"hold"`` (keep the
    current setpoint, no write), ``"cap"`` (a prediction re-command was wanted but the per-session
    command budget is spent → hold+warn) or ``"cap_reconcile"`` (a RECONCILE re-command was wanted
    but the budget is spent → the caller must fall back to the safe HOLD path, never keep holding a
    discharge setpoint the battery never adopted).

    Layered on top of `car_action.recommand` (car_mode's own bounded re-command rule) are two extra
    safety gates in the wiring: a >= `dwell` gap between prediction-driven commands, and a hard
    `max_commands` ceiling per session. This keeps a whole charging session to a handful of writes
    even with a noisy prediction.

    F1 (observed-mode reconciliation): a first write that returns 'unconfirmed' advances the box as
    if it applied, so with a stable prediction the ordinary re-command never fires again and the
    battery can sit in vendor AUTO draining into the car all session. So, independently of the
    prediction, if the session is ACTIVE but the battery is OBSERVED to not be DISCHARGE (a known
    non-DISCHARGE mode), or the last command's outcome was unconfirmed/failed, a re-command is DUE —
    on a SHORT `reconcile_spacing` (>= 1 control cycle, NOT the 10-min prediction dwell; this is
    safety reconciliation), still bounded by `max_commands`."""
    active = bool(session.get("active"))
    setpoint = session.get("setpoint_w")
    commands = int(session.get("commands") or 0)
    commanded_at = session.get("commanded_at")
    last_outcome = session.get("last_outcome")
    first = not active
    elapsed: timedelta | None = None
    if commanded_at:
        try:
            elapsed = now - datetime.fromisoformat(commanded_at)
        except (TypeError, ValueError):
            elapsed = None  # a corrupt timestamp must never wedge the session
    dwell_ok = elapsed is None or elapsed >= dwell
    reconcile_ok = elapsed is None or elapsed >= reconcile_spacing

    # F1: is a safety reconciliation re-command due? Only for an ACTIVE session, when we can see the
    # setpoint didn't take: a KNOWN non-DISCHARGE observed mode (None = unreadable, don't assume
    # drift), or a bad last outcome. Spaced by reconcile_spacing, not the 10-min prediction dwell.
    battery_not_discharging = (
        observed_mode is not None and observed_mode is not PhysicalMode.DISCHARGE)
    reconcile = active and reconcile_ok and (
        battery_not_discharging or last_outcome in _CAR_BAD_OUTCOMES)

    ordinary = car_action.recommand and (first or dwell_ok)
    want = ordinary or reconcile
    if want and commands >= max_commands:
        # Budget spent. If a reconcile was due, holding a discharge setpoint the battery never
        # adopted just keeps draining into the car — the caller must fall back to the safe HOLD.
        return False, dict(session, active=True), ("cap_reconcile" if reconcile else "cap")
    if want:
        nxt = {"active": True, "setpoint_w": car_action.power_w,
               "commanded_at": now.isoformat(), "commands": commands + 1}
        event = "start" if first else ("reconcile" if reconcile and not ordinary else "recommand")
        return True, nxt, event
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


def _decide_grace_action(
    *, override_active: bool, failsafe: bool, soc_pct: float, min_reserve_soc: float,
) -> str:
    """PURE. Within the below-threshold GRACE window (a car session is active and the car dipped
    below the charging threshold, but the end-hysteresis has not yet elapsed), decide what to do
    THIS cycle. The grace window's job is to bridge a brief EV-power dip WITHOUT resuming the plan;
    but three things must still act immediately rather than be swallowed by the grace hold:

      * ``"fall_through"`` — a manual override or a data-quality fail-safe wants the battery NOW;
        the caller ends the session and lets the ordinary decide() apply the effective intent this
        cycle (F3: the grace short-circuit must never swallow an override / fail-safe).
      * ``"reserve_hold"`` — SoC is at/within the reserve floor band; the caller ends the session
        and holds at the reserve floor now, rather than keep discharging the last setpoint through
        the grace window on a nearly-drained battery (F5).
      * ``"hold"`` — a genuine benign EV-power blip; hold the current setpoint through the grace
        window (unchanged behaviour — the reason the grace window exists).

    An override (deliberate operator action) takes precedence over the reserve floor."""
    if override_active or failsafe:
        return "fall_through"
    if soc_pct <= min_reserve_soc + _RESERVE_ENTER_PP:
        return "reserve_hold"
    return "hold"


def _commit_hysteresis_state(
    box: dict, lock: threading.Lock, new_state: HysteresisState,
    cache_store: object | None, key: str, ttl: float, log: logging.Logger = _log,
) -> bool:
    """Thread-safe adopt-and-persist of a seasonal-transition `HysteresisState` (F6).

    `_commit_hysteresis` is reached from THREE unsynchronised threads — the periodic control loop,
    an override-triggered control cycle, and a synchronous dashboard read that resolves the strategy
    — each doing a read-modify-write on the shared `box` plus a `cache_store.set`. Without a lock
    two writers can interleave the box assignment and the persist, losing an update / corrupting the
    KV write. This serialises the whole RMW+persist under `lock`. Returns whether the state changed
    (the TTL is refreshed on every call either way, so a quiet season doesn't expire). Best-effort
    persist: a cache hiccup must never break strategy resolution."""
    with lock:
        changed = new_state != box["state"]
        box["state"] = new_state
        if cache_store is not None:
            try:
                cache_store.set(key, new_state.to_json(), ttl)
            except Exception:
                log.debug("hysteresis state persist failed (non-fatal)", exc_info=True)
        return changed


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
    # Serialise the hysteresis read-modify-write + persist across the three threads that reach
    # `_commit_hysteresis_state` (periodic loop / override cycle / sync dashboard read) — see F6. A
    # threading.Lock (NOT asyncio.Lock): those paths run synchronously via asyncio.to_thread.
    hysteresis_lock: threading.Lock = field(default_factory=threading.Lock)
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
    # Intent-persistence anti-flap state (07-12 guardrail-starvation incident: 13 routine
    # auto<->idle flaps burned the daily switch cap by 09:48 and then starved a COMMITTED
    # grid-charge). A ROUTINE (non-AUTO) intent change must be OBSERVED for
    # control.intent_persistence_cycles consecutive cycles before it may command the battery.
    # `mode` = the routine desired mode currently accumulating consecutive cycles (or None);
    # `count` = how many consecutive cycles it has been the pending routine switch. The one-row
    # dedup of the "confirming next cycle" audit rides the existing held_box (cleared on any real
    # write), so a flap costs at most one row per flap, never one per cycle.
    intent_persist_box: dict[str, Any] = field(
        default_factory=lambda: {"mode": None, "count": 0})
    # Captured by control_tick after effective_intent returns so a control.overrun audit row can
    # attribute the breach to the value the tick reached (B-80 task 4 review). Read by
    # _handle_overrun. None when no tick has run yet (or the tick timed out before reaching
    # effective_intent, e.g. hung in the sense phase).
    intended_mode_box: dict[str, Any] = field(default_factory=lambda: {"value": None})
    # Car-charging discharge session (feat/car-charge-modes). IN-MEMORY ONLY: a restart mid-session
    # simply starts a fresh session and re-commands ONCE next cycle (documented, acceptable). Keys:
    # {active, setpoint_w (last commanded W), commanded_at (iso of last car command), commands,
    # below_threshold_cycles (the end-hysteresis counter, see car_session_end_if_active),
    # reserve_hold (the F2 reserve-floor hysteresis latch), last_outcome (the last car command's
    # controller outcome, for the F1 observed-mode reconciliation)}.
    car_session: dict[str, Any] = field(default_factory=lambda: {
        "active": False, "setpoint_w": None, "commanded_at": None, "commands": 0,
        "below_threshold_cycles": 0, "reserve_hold": False, "last_outcome": None})
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
        control_cycle_seconds: float = 300.0,
        # B-46 stage 2: the coalesced live reads need the meter/battery `source` and the seasonal
        # strategy resolution needs the KV `cache_store`; both are OPTIONAL so a unit test can build
        # the service with only the injected callables below (no hardware, no cache).
        source: Any | None = None,
        cache_store: Any | None = None,
        # --- injected callables ------------------------------------------------------------------
        # `data_quality`/`validate_plan_obj` stay api.py closures (freshness- and capability-bound,
        # web-facing) and are always injected. The rest are the reads / config builders / strategy
        # resolution B-46 stage 2 moved INTO this service as methods: they default to those methods,
        # so api.py leaves them None (the service is self-contained), while test_control_service
        # keeps injecting trivial stand-ins (no `source`/`cache_store` needed) — construction and
        # every behaviour-through-the-closures test are byte-for-byte unchanged.
        data_quality: Callable[[datetime], str],
        validate_plan_obj: Callable[[Any, datetime], PlanValidation],
        current_soc: Callable[[datetime], float] | None = None,
        current_mode: Callable[[datetime], PhysicalMode | None] | None = None,
        current_towers: Callable[[datetime], Any] | None = None,
        car_charging: Callable[[datetime], bool] | None = None,
        load_by: Callable[[list[datetime]], dict[datetime, float]] | None = None,
        active_strategy: Callable[[datetime], str] | None = None,
        planner_cfg: Callable[[], Any] | None = None,
        summer_cfg: Callable[[float], Any] | None = None,
        adaptive_cfg: Callable[[], Any] | None = None,
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
        self._control_cycle_seconds = control_cycle_seconds
        self._source = source
        self._cache_store = cache_store
        self._data_quality = data_quality
        self._validate_plan_obj = validate_plan_obj
        # Resolve each moved dependency to the injected stand-in when given (tests), else this
        # service's own method (production). Internals call `self._<name>` throughout, unchanged.
        self._current_soc = current_soc if current_soc is not None else self.current_soc
        self._current_mode = current_mode if current_mode is not None else self.current_mode
        self._current_towers = (
            current_towers if current_towers is not None else self.current_towers)
        self._car_charging = car_charging if car_charging is not None else self.car_charging
        self._load_by = load_by if load_by is not None else self.load_by
        self._active_strategy = (
            active_strategy if active_strategy is not None else self.active_strategy)
        self._planner_cfg = planner_cfg if planner_cfg is not None else self.planner_cfg
        self._summer_cfg = summer_cfg if summer_cfg is not None else self.summer_cfg
        self._adaptive_cfg = adaptive_cfg if adaptive_cfg is not None else self.adaptive_cfg

    # --- coalesced live reads / config builders / strategy resolution (B-46 stage 2) -------------
    # Moved verbatim from api.py's create_app closures. Kept here because their primary caller is
    # the control cycle (plan path, effective-intent, tick); api.py aliases them so its endpoints
    # keep calling the same names. Pure wrt logic — the only state is `ctx`'s coalescing caches.

    def _coalesce_s(self) -> float:
        """How long a live read is reused before re-reading the hardware (UI-tunable, eases load on
        a battery shared with Home Assistant + the Indevolt app); falls back to the module
        default."""
        try:
            return float(self._settings.get("control.live_read_seconds")
                         or _LIVE_SAMPLE_COALESCE_SECONDS)
        except (TypeError, ValueError):
            _log.debug("invalid control.live_read_seconds; default (non-fatal)", exc_info=True)
            return _LIVE_SAMPLE_COALESCE_SECONDS

    def _sample_fresh(self, now: datetime) -> bool:
        cache = self._ctx.sample_cache
        at = cache["at"]
        return (at is not None and cache["sample"] is not None
                and (now - at).total_seconds() < self._coalesce_s())

    def current_sample(self, now: datetime):
        if self._sample_fresh(now):  # fast path: no lock when the cache is warm
            return self._ctx.sample_cache["sample"]
        with self._ctx.sample_lock:  # single-flight: one thread reads hardware/window, others reuse
            if self._sample_fresh(now):
                return self._ctx.sample_cache["sample"]
            try:
                self._ctx.sample_cache["sample"], self._ctx.sample_cache["at"] = (
                    self._source.read(), now)
            except Exception:
                _log.debug("live sample read failed; keeping last good (non-fatal)", exc_info=True)
                pass  # keep the last good sample (fail-safe)
            return self._ctx.sample_cache["sample"]

    def current_soc(self, now: datetime) -> float:
        s = self.current_sample(now)
        return float(s.soc_pct) if s is not None else 0.0

    def _towers_fresh(self, now: datetime) -> bool:
        cache = self._ctx.tower_cache
        at = cache["at"]
        return (at is not None and cache["towers"] is not None
                and (now - at).total_seconds() < self._coalesce_s())

    def current_towers(self, now: datetime):
        """Coalesced + single-flight per-tower battery read (same window as current_sample). Returns
        the cached list of TowerReading, or None when there's no cluster reader (mock). On a read
        failure the last good snapshot is kept (fail-safe). The lock means several tabs hitting
        /api/battery at cache expiry poll the cluster ONCE, not once each."""
        reader = getattr(self._source, "battery", None)
        if reader is None or not hasattr(reader, "read_towers"):
            return None
        if self._towers_fresh(now):  # fast path
            return self._ctx.tower_cache["towers"]
        with self._ctx.tower_lock:
            if self._towers_fresh(now):
                return self._ctx.tower_cache["towers"]
            try:
                self._ctx.tower_cache["towers"], self._ctx.tower_cache["at"] = (
                    reader.read_towers(), now)
            except Exception:
                _log.debug("per-tower read failed; keeping last good (non-fatal)", exc_info=True)
                pass  # keep last good snapshot (fail-safe)
            return self._ctx.tower_cache["towers"]

    def current_mode(self, now: datetime):
        """The battery's current physical mode, DERIVED from the shared coalesced cluster read
        (current_towers) — so the dashboard previews AND the control loop reuse that one read
        instead of each hitting the master with its own driver.current_mode(). None in dry-run / no
        controller. Falls back to a direct driver read only when there's no cluster reader at
        all."""
        if self._controller is None or self._dry_run:
            return None
        towers = self._current_towers(now)
        if towers is not None:  # cluster reader present → reuse its coalesced read, not the master
            cand = next((t for t in towers if t.role == "master" and t.online and t.mode), None) \
                or next((t for t in towers if t.online and t.mode), None)
            return _LABEL_TO_MODE.get(cand.mode) if cand is not None else None
        try:
            return self._controller.driver.current_mode()
        except Exception:
            _log.debug("battery mode read failed (non-fatal)", exc_info=True)
            return None

    def battery_reachable(self, now: datetime) -> bool:
        """Whether the battery cluster answered THIS read window — reuses the same coalesced reads
        as everything else (no new device read). Any tower online counts; with no cluster reader
        this falls back to whether the last coalesced sample read succeeded (confidence score)."""
        towers = self._current_towers(now)
        if towers is not None:
            return any(t.online for t in towers)
        return self.current_sample(now) is not None

    def car_charging(self, now: datetime) -> bool:
        s = self.current_sample(now)
        return s is not None and float(s.ev_power_w) > self._settings[
            "control.car_charging_threshold_w"]

    def planner_cfg_from(self, s: dict) -> PlannerConfig:
        return PlannerConfig(
            round_trip_efficiency=s["planner.round_trip_efficiency"],
            degradation_eur_per_kwh=s["planner.degradation_eur_per_kwh"],
            risk_margin_eur_per_kwh=s["planner.risk_margin_eur_per_kwh"],
            charge_slots=s["planner.charge_slots"],
            discharge_slots=s["planner.discharge_slots"],
            negative_price_soak=s["planner.negative_price_soak"],
        )

    def planner_cfg(self) -> PlannerConfig:
        return self.planner_cfg_from(self._settings)

    def night_target_soc(self, soc_pct: float):
        """The night-carry target (overnight load + reserve + floor), via compute_charge_need."""
        s = self._settings
        return compute_charge_need(
            soc_pct=soc_pct, usable_kwh=s["battery.usable_kwh"],
            min_reserve_soc=s["battery.min_reserve_soc"],
            night_reserve_kwh=s["battery.night_reserve_kwh"],
            overnight_load_kwh=s["battery.overnight_load_kwh"],
            round_trip_efficiency=s["planner.round_trip_efficiency"],
        )

    def summer_cfg(self, soc_pct: float) -> SummerConfig:
        s = self._settings
        return SummerConfig(
            usable_kwh=s["battery.usable_kwh"],
            target_soc_pct=self.night_target_soc(soc_pct).target_soc_pct,
            round_trip_efficiency=s["planner.round_trip_efficiency"],
            max_charge_w=s["battery.max_charge_w"],
            expected_load_w=s["battery.overnight_load_kwh"] * 1000.0 / 12.0,
            solar_confidence=s["planner.solar_confidence"] / 100.0,
            allow_grid_topup=s["strategy.summer_grid_topup"],
            max_topup_price_eur_per_kwh=s["strategy.summer_max_topup_price"],
            negative_price_soak=s["planner.negative_price_soak"],
        )

    def adaptive_cfg(self) -> AdaptiveConfig:
        s = self._settings
        return AdaptiveConfig(
            usable_kwh=s["battery.usable_kwh"],
            reserve_soc_pct=s["battery.min_reserve_soc"],
            round_trip_efficiency=s["planner.round_trip_efficiency"],
            max_charge_w=s["battery.max_charge_w"],
            degradation_eur_per_kwh=s["planner.degradation_eur_per_kwh"],
            risk_margin_eur_per_kwh=s["planner.risk_margin_eur_per_kwh"],
            solar_confidence=s["planner.solar_confidence"] / 100.0,
            negative_price_soak=s["planner.negative_price_soak"],
        )

    def load_by(self, starts: list[datetime]) -> dict[datetime, float]:
        prof = self._ctx.load_profile_box["profile"]
        if prof is None:  # cold start: a flat overnight-derived baseline
            fallback = self._settings["battery.overnight_load_kwh"] * 1000.0 / 12.0
            return {s: fallback for s in starts}
        return {s: prof.expected_w(s) for s in starts}

    def strategy_inputs(self, now: datetime):
        """(surplus_kwh, price_spread_eur) over the next ~24h, for the energy-condition `auto`
        strategy choice. Defensive — any failure yields None so it falls back to the season."""
        surplus = spread = None
        try:
            if self._solar_forecast is not None:
                fc = self._solar_forecast.slots()[:96]
                load = self._load_by([f.start for f in fc])
                surplus = sum(max(0.0, f.p50_w - load.get(f.start, 0.0)) * 0.25 / 1000.0
                              for f in fc)
        except Exception:
            _log.debug("strategy surplus estimate failed (non-fatal)", exc_info=True)
        try:
            if self._price_source is not None:
                ps = [p.eur_per_kwh for p in self._price_source.slots()[:96]]
                if ps:
                    spread = max(ps) - min(ps)
        except Exception:
            _log.debug("strategy price-spread estimate failed (non-fatal)", exc_info=True)
        return surplus, spread

    def commit_hysteresis(self, new_state: HysteresisState) -> None:
        """Adopt `new_state` and persist it to the KV cache so the pending-switch counter survives a
        restart (§8.4 / B-15). Thread-safe (F6) via ctx.hysteresis_lock; best-effort persist."""
        _commit_hysteresis_state(
            self._ctx.hysteresis_box, self._ctx.hysteresis_lock, new_state, self._cache_store,
            HYSTERESIS_KEY, HYSTERESIS_TTL_SECONDS,
        )

    def resolve_strategy(self, now: datetime) -> tuple[str, str]:
        """(strategy, reason). Forced modes skip the (cheap-but-unneeded) energy-input computation;
        `auto` decides by forecast surplus + price spread, then dampened by the seasonal-transition
        hysteresis (SPEC §8.4 / B-15) so shoulder-month days can't flap."""
        mode = self._settings["strategy.mode"]
        hyst_days = int(self._settings["strategy.hysteresis_days"])
        if mode in ("summer", "winter"):
            surplus = spread = None
        else:
            surplus, spread = self.strategy_inputs(now)
        strat, why, new_state = resolve_strategy_hysteretic(
            now, mode, self._site_tz, self._ctx.hysteresis_box["state"],
            surplus_kwh=surplus, price_spread_eur=spread, hysteresis_days=hyst_days,
        )
        self.commit_hysteresis(new_state)
        return strat, why

    def active_strategy(self, now: datetime) -> str:
        return self.resolve_strategy(now)[0]

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
            # F2: carry the reserve-floor hold across cycles so the two-threshold hysteresis (enter
            # +1pp, resume +3pp) actually damps SoC noise around the floor instead of flapping.
            reserve_holding=bool(self._ctx.car_session.get("reserve_hold")),
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
            # F2: a RESERVE-floor hold that interrupts an ACTIVE discharge session is surfaced (the
            # car_action, not None) so the control tick keeps the session alive across the sticky,
            # hysteretic hold instead of ending+restarting it (which flapped on floor noise). Every
            # other hold (the master "hold" behaviour, or a reserve hold with no session) returns
            # None — today's behaviour byte-for-byte, a plain HOLD_RESERVE.
            if car_action.reserve_hold and self._ctx.car_session["active"]:
                return BatteryIntent.HOLD_RESERVE, car_action.reason, car_action
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
                                     below_threshold_cycles=0, reserve_hold=False,
                                     last_outcome=None)

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
        `unconfirmed` outcome — we hold, we do NOT revert).

        F1: the command decision also consults the OBSERVED battery mode (from the shared coalesced
        read — never a fresh device read) and the last command's outcome, so a discharge that never
        actually took (unconfirmed first write, or the vendor drifting back to AUTO) is reconciled
        by re-commanding on a short (>= 1 cycle) spacing — not left silently draining into the car.
        When the reconciliation budget is spent, it falls back to the safe HOLD (idle) path.

        Car commands pass count_toward_cap=False so they don't spend the planner's daily switch
        budget (F4) — their cadence is already bounded by the car session's own gates."""
        session = self._ctx.car_session
        command, nxt, event = _decide_car_command(
            session, car_action, now, observed_mode=observed,
            reconcile_spacing=timedelta(seconds=self._control_cycle_seconds))
        power = car_action.power_w
        if not command:
            if event == "cap_reconcile":
                # F1: the discharge setpoint never took AND the re-command budget is spent — stop
                # chasing DISCHARGE and fall back to the guard's safe HOLD (idle), idempotently each
                # cycle, so the battery can't keep draining into the car. The safe terminal state.
                with timed("control.write"):
                    dec = self._controller.decide(
                        BatteryIntent.HOLD_RESERVE, now, observed_mode=observed,
                        manual=override_active, priority=True, count_toward_cap=False)
                session.update(nxt)
                session["last_outcome"] = dec.outcome
                return [{"summary": ("car session: command budget spent and the battery never "
                                     "took the discharge — holding at reserve (idle) so it can't "
                                     "drain into the car"),
                         "detail": {"event": "car_session_cap_reconcile", "outcome": dec.outcome,
                                    "commands": session["commands"]}}]
            session.update(nxt)  # keep the session alive (hold the current setpoint, no write)
            if event == "cap":
                return [{"summary": ("car session: command budget spent — holding the current "
                                     f"setpoint (~{session['setpoint_w']:.0f} W), not "
                                     "re-commanding a moving prediction"),
                         "detail": {"event": "car_session_cap",
                                    "setpoint_w": session["setpoint_w"],
                                    "commands": session["commands"]}}]
            return []  # quiet hold — the normal steady state of a car session (no audit spam)
        with timed("control.write"):
            dec = self._controller.decide(
                BatteryIntent.DISCHARGE_FOR_LOAD, now, target_soc=target_soc, power_w=power,
                observed_mode=observed, manual=override_active, priority=True,
                car_session=True, force=True, count_toward_cap=False)
        # Any attempted device write advances the session (bounds retries via the cap); dry-run /
        # not-controlling did not touch the device, so the session doesn't advance on those.
        if dec.outcome in _CAR_ATTEMPTED_OUTCOMES:
            session.update(nxt)
        else:
            # dry_run / not_controlling — no device write (unreachable here in prod: the tick gates
            # on lc.can_command and dry-run never runs the tick). Seed a coherent box anyway so a
            # later real command has a setpoint to compare against (no every-cycle recommand loop).
            session.update(active=True, setpoint_w=power)
        # F1: record the outcome so next cycle's reconciliation can tell whether the write took.
        session["last_outcome"] = dec.outcome
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

    # --- intent-persistence anti-flap ------------------------------------------------------------
    def _intent_persistence_cycles(self) -> int:
        """How many consecutive cycles a ROUTINE intent change must persist before it commands the
        battery (control.intent_persistence_cycles; 1 = legacy, no smoothing). Defensive: any bad
        value falls back to 1 so a broken setting can never freeze control."""
        try:
            return max(1, int(self._settings["control.intent_persistence_cycles"]))
        except (TypeError, ValueError, KeyError):
            _log.debug("invalid control.intent_persistence_cycles; no smoothing (non-fatal)",
                       exc_info=True)
            return 1

    def _confirm_routine_intent(
        self, intent: BatteryIntent, desired: PhysicalMode, cycles: int,
    ) -> list[dict] | None:
        """Anti-flap gate for a ROUTINE (non-AUTO, non-commitment, non-priority, non-override) mode
        change. Returns None to let this cycle's decide() ACT, or a (possibly empty) list of audit
        records to OBSERVE this cycle instead of writing.

        WHY (07-12 guardrail-starvation incident): 13 routine auto<->idle flaps exhausted the
        10-switch daily cap by 09:48; the afternoon's committed grid-charge was then cap_reached-
        blocked five times and missed its deadline by 66 min. Requiring a routine change to persist
        `cycles` consecutive control cycles (first observes, second acts) means a transient flap
        never spends a switch, so the cap is there when a commitment needs it. Side benefit: fewer
        writes / less wear ('<10 writes/day' goal).

        The "confirming next cycle" hold is deduped via held_box (which the tick clears on any real
        write / idempotent), so a multi-cycle hold is one row and a flap is at most one row per
        flap — never one row per cycle (explainability first, but not spam)."""
        box = self._ctx.intent_persist_box
        if box["mode"] == desired.value:
            box["count"] += 1
        else:  # a different pending routine mode (or the first appearance) — restart the count
            box.update(mode=desired.value, count=1)
        if box["count"] >= cycles:
            box.update(mode=None, count=0)  # confirmed — act now (re-arm for the next change)
            return None
        sig = ("intent_pending", desired.value)
        if self._ctx.held_box["sig"] == sig:
            return []  # already explained this pending switch (deduped)
        self._ctx.held_box["sig"] = sig
        reason = (f"intent changed to {intent.value} — confirming for {cycles} cycles before "
                  f"switching to {desired.value} (anti-flap)")
        return [{"summary": f"Battery NOT switched to {desired.value} yet — {reason}",
                 "detail": {"desired_mode": desired.value, "intent": str(intent),
                            "outcome": "intent_pending", "reason": reason,
                            "override_active": False}}]

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
        # Wrapped in `control.sense` for B-80 perf attribution (dominant-phase sample on overrun).
        with timed("control.sense"):
            if self._data_quality(now) != "unsafe":
                lc.mark_sensors_validated()
            # Reachability + idempotency reuse the SHARED coalesced cluster read (observed) instead
            # of a separate per-cycle master mode-read — far gentler on a device shared with HA +
            # the app. IMPORTANT: "reachable" = the battery RESPONDED this cycle (a tower online),
            # NOT that its mode decoded to a known label — else an unexpected mode value would stall
            # ALL control (incl. manual overrides). `observed` may be None; decide() reads fresh.
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
        with timed("control.decide"):
            intent, _reason, override_active, tgt, pw, _v, car_action = self.effective_intent(now)
            # captured for the control.overrun audit detail (B-80 task 4 review)
            intended_mode = tgt
            self._ctx.intended_mode_box["value"] = intended_mode
        if intent is None:
            # End a dangling session, nothing else to do — [] both when already inactive and when
            # the end-hysteresis grace window is still open (nothing to act on either way).
            return self.car_session_end_if_active(now) or []
        # A car-charging discharge session owns its own bounded command cadence (a real DISCHARGE at
        # the covered-house setpoint) — handled separately from the ordinary single-write path.
        if car_action is not None and car_action.action == "discharge":
            self._ctx.car_session["below_threshold_cycles"] = 0  # car read above threshold this cyc
            self._ctx.car_session["reserve_hold"] = False  # F2: discharging → not in reserve hold
            return self.car_session_command(now, car_action, tgt, override_active, observed)
        # F2: a RESERVE-floor hold that interrupts an ACTIVE session (car still charging, SoC in the
        # floor band). Keep the session ALIVE and idle the battery through the ordinary idempotent
        # HOLD path; reserve_hold sticks (resume only at +3pp) so floor noise can't flap it. Do NOT
        # run the end hysteresis — this is not the car stopping, and a reserve hold must not end the
        # session (it resumes discharging once SoC recovers).
        if (car_action is not None and car_action.action == "hold"
                and car_action.reserve_hold and self._ctx.car_session["active"]):
            entering = not self._ctx.car_session.get("reserve_hold")
            self._ctx.car_session["reserve_hold"] = True
            self._ctx.car_session["below_threshold_cycles"] = 0
            with timed("control.write"):
                dec = self._controller.decide(
                    BatteryIntent.HOLD_RESERVE, now, observed_mode=observed,
                    manual=override_active, priority=True, count_toward_cap=False)
            self._ctx.car_session["last_outcome"] = dec.outcome
            if entering:
                return [{"summary": f"car session held at the reserve floor — {car_action.reason}",
                         "detail": {"event": "car_session_reserve_hold", "outcome": dec.outcome}}]
            return []  # steady reserve hold — quiet (idempotent downstream)
        # F2 (production audit: ~15 write timeouts/week, ALL inside ~10 kW car-charging windows —
        # the Indevolt's single embedded HTTP server saturates and register writes time out). While
        # a car discharge session is ACTIVE and the car is still drawing under car-mode management,
        # DEFER a non-safety planner grid-charge instead of writing into the saturated device: no
        # command, no daily-switch-cap / dwell spend, and the session stays alive. The B-16
        # recovery/replan path re-issues the charge on the first post-session tick if it's still
        # wanted. This NEVER defers a safety action: a manual override (override_active) is a
        # deliberate priority command; the car-guard hold and the reserve hold act ABOVE (they set
        # car_action and return); return-to-AUTO and the data fail-safe substitute self-consumption
        # (AUTO), not GRID_CHARGE — so gating on GRID_CHARGE_TO_TARGET + not override_active is
        # sufficient. Gated on the car ACTUALLY charging under the master switch so the deferral
        # releases the instant the car stops (or the switch is off), letting the session end and the
        # charge apply. Deduped via `held_box` so a long car+charge window is explained ONCE, not a
        # row per cycle (explainability first, but not spam — mirrors a recurring held decision).
        if (self._ctx.car_session["active"] and not override_active
                and intent is BatteryIntent.GRID_CHARGE_TO_TARGET
                and self._settings["control.hold_battery_when_car_charging"]
                and self._car_charging(now)):
            sig = ("deferred", PhysicalMode.CHARGE.value)
            if self._ctx.held_box["sig"] == sig:
                return []  # already explained this deferral episode (deduped like a held decision)
            self._ctx.held_box["sig"] = sig
            reason = ("car charging in progress — deferring grid-charge command to avoid Indevolt "
                      "write timeouts; will retry after session ends")
            return [{"summary": reason,
                     "detail": {"desired_mode": PhysicalMode.CHARGE.value, "intent": str(intent),
                                "outcome": "deferred", "reason": reason,
                                "override_active": override_active}}]
        # Not a car discharge/reserve-hold this cycle: if a session was active (car dipped below
        # threshold / master off / plan resumed) decide whether to end it now
        # (see car_session_end_if_active) — a below-threshold dip gets a few cycles' grace, so
        # `None` means "still in the grace window".
        records = self.car_session_end_if_active(now)
        if records is None:
            # Within the below-threshold GRACE window. The grace hold exists to bridge a benign
            # EV-power blip WITHOUT resuming the plan — but three things must still act THIS cycle
            # and never be swallowed by the hold: a manual override or data-quality fail-safe (F3),
            # and the reserve floor (F5). Only a genuine blip holds the current setpoint.
            failsafe = self._data_quality(now) == "unsafe" or (_v is not None and not _v.ok)
            grace = _decide_grace_action(
                override_active=override_active, failsafe=failsafe, soc_pct=self._current_soc(now),
                min_reserve_soc=self._settings["battery.min_reserve_soc"])
            if grace == "hold":
                return []  # benign blip — hold the current setpoint through the grace window
            self.car_session_reset()  # F3/F5: end the session; act THIS cycle
            if grace == "reserve_hold":
                with timed("control.write"):
                    dec = self._controller.decide(BatteryIntent.HOLD_RESERVE, now,
                                                  observed_mode=observed, manual=override_active,
                                                  priority=True, count_toward_cap=False)
                return [{"summary": "car session ended at the reserve floor — holding (idle) so "
                                    "the battery can't drain into the car",
                         "detail": {"event": "car_session_end", "reason": "reserve_floor",
                                    "outcome": dec.outcome}}]
            # grace == "fall_through": end + fall through to apply the override/fail-safe via the
            # ordinary decide() below, so it takes effect THIS cycle (not after the grace elapses).
            records = [{"summary": "car session ended — override/fail-safe takes precedence",
                        "detail": {"event": "car_session_end", "reason": "override_or_failsafe"}}]
        # decide() uses `observed` for the idempotency gate; its post-write CONFIRM re-reads the
        # device fresh, so a stale observation only risks a redundant idempotent write. `manual` (an
        # active operator override) and `priority` (a SAFETY action — the car-guard hold while the
        # car charges) bypass the automatic dwell/cap gates: never leave the battery draining into
        # the car just because today's switch budget is spent (a return to AUTO is always allowed
        # too; see _gate).
        priority = self._car_charging(now)
        commitment = intent is BatteryIntent.GRID_CHARGE_TO_TARGET
        # Intent-persistence anti-flap (07-12 starvation incident — see _confirm_routine_intent).
        # A ROUTINE (non-AUTO) mode change must persist a few cycles before it commands the battery,
        # so a transient auto<->idle flap can't burn the daily cap and starve a committed charge.
        # EXEMPT — act immediately, exactly as before: a manual override (override_active), a
        # car-charging safety context (priority), a committed grid-charge (commitment), and any
        # return-to-AUTO / fail-safe (a desired AUTO is never held — mirrors _gate). Only when a
        # switch is actually PENDING (desired differs from the observed mode) is it worth holding.
        cycles = self._intent_persistence_cycles()
        desired = intent_to_mode(
            intent, allow_export_discharge=self._controller.allow_export_discharge)
        routine_pending = (
            cycles > 1 and not override_active and not priority and not commitment
            and desired is not PhysicalMode.AUTO
            and (observed is None or desired is not observed))
        if routine_pending:
            hold = self._confirm_routine_intent(intent, desired, cycles)
            if hold is not None:  # observe this cycle; a later cycle acts once it persists
                return records + hold
        else:  # exempt / idempotent / a switch just confirmed — re-arm the anti-flap counter
            self._ctx.intent_persist_box.update(mode=None, count=0)
        with timed("control.write"):
            dec = self._controller.decide(intent, now, target_soc=tgt, power_w=pw,
                                          observed_mode=observed, manual=override_active,
                                          priority=priority, commitment=commitment)
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
            # got it; reverting would also time out). Surface it so a recurring "charge isn't
            # sticking because the battery is slow to answer" is visible, not silent — but let the
            # controller's F3 episode de-dupe decide whether THIS unconfirmed is a NEW incident row
            # via `dec.audit`: True for the first unconfirmed of a stuck episode and again once
            # ~60 min pass, False for the repeats in between. One incident per episode (with hourly
            # evidence for a long outage), not the live 13-row-per-episode inflation. This is the
            # dec.audit consumer (F3) — the gate lives in the controller; the tick just honours it,
            # which SUPERSEDES the held-box latch here (the latch would swallow the hourly re-log).
            # A write was attempted, so clear held["sig"] like the applied/failed paths — a later
            # genuine dwell/cap hold then re-explains afresh.
            held["sig"] = None
            if dec.audit:
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
        an immediate override-triggered run can't overlap (two concurrent writes to the battery).

        Wrapped with a hard `asyncio.wait_for(..., timeout=PERF_BUDGETS["control.cycle"])` deadline
        (B-80): a hung tick (deadlock, infinite loop, blocked device read) cannot stall the loop
        indefinitely. On overrun we audit-log the event and force the battery to AUTO via the
        single-writer seam — but ONLY when the lifecycle allows commanding AND we are not in
        dry-run. See design spec §4.3."""
        if self._controller is None or self._dry_run:
            return
        async with self._ctx.control_lock:
            now = datetime.now(UTC)
            await self.refresh_car_obs(now)  # warm the house-load prediction before the (sync) tick
            timed_out = False
            records: list[dict] = []
            try:
                async with atimed("control.cycle"):
                    try:
                        records = await asyncio.wait_for(
                            asyncio.to_thread(self.control_tick, now),
                            timeout=PERF_BUDGETS["control.cycle"] / 1000.0,  # ms -> s
                        )
                    except TimeoutError:
                        timed_out = True
                        _log.warning("control.overrun: cycle exceeded %.1fs budget (timeout)",
                                     PERF_BUDGETS["control.cycle"] / 1000.0)
                    except Exception:
                        _log.exception("control tick failed; retry next cycle (fail-safe)")
                        return
            except Exception:
                # Defensive: atimed itself should never raise, but a registry push failure must not
                # wedge the control loop.
                pass
            recent = REGISTRY.recent("control.cycle")
            if recent and (recent[-1].over_budget or timed_out):
                await self._handle_overrun(now, timed_out, recent[-1])
            async with atimed("control.audit"):
                for rec in records:
                    if self._audit_store is not None:
                        try:
                            await self._audit_store.append(now.isoformat(), "battery_decision",
                                                           rec["summary"], rec["detail"])
                        except Exception:
                            _log.warning("failed to write battery-decision audit", exc_info=True)

    async def _handle_overrun(self, now: datetime, timed_out: bool, sample) -> None:
        """Force the battery to AUTO if not dry-run AND the lifecycle allows commanding; otherwise
        log only. ALWAYS audit-logs the event (a budget breach with no device write is still a
        budget breach). The three guard layers in order:

        1. dry-run — never write the device, but still audit (operators need to see overruns even
           when the writer is disarmed);
        2. lifecycle grace — if we're not yet CONTROLLING (sensors not validated, battery
           unreachable, plan not loaded) we MUST NOT issue a forced write either — `can_command` is
           the single gate every other write goes through, and bypassing it here would be a
           foot-gun;
        3. the AUTO write itself is wrapped in try/except so an unreachable driver during the
           recovery is logged-and-swallowed (non-fatal: the next cycle re-attempts via the normal
           path, and the audit row already records the original overrun)."""
        if self._audit_store is not None:
            try:
                intended_mode = self._ctx.intended_mode_box["value"]
                await self._audit_store.append(
                    now.isoformat(), "control.overrun",
                    f"control cycle exceeded budget: {sample.duration_ms:.0f} ms",
                    {
                        "duration_ms": sample.duration_ms,
                        "reason": "timeout" if timed_out else "duration",
                        "phase": _dominant_phase(),
                        "intended_mode": (str(intended_mode)
                                          if intended_mode is not None else None),
                    },
                )
            except Exception:
                _log.warning("failed to write control-overrun audit", exc_info=True)
        if self._dry_run:
            return
        lc = self._controller.lifecycle if self._controller is not None else None
        if lc is None or not lc.can_command(now):
            return
        try:
            await self._controller.driver.apply(PhysicalMode.AUTO)
            _log.warning("control.overrun: forced battery to AUTO")
        except Exception:
            _log.exception("control.overrun: AUTO write failed (non-fatal)")


def _dominant_phase() -> str | None:
    """Name of the slowest `control.<phase>` sample so far in this cycle, or None when no phase
    samples have been pushed yet (e.g. a tick timed out before reaching any phase). Read by
    `_handle_overrun` to attribute the overrun to its likely cause on the audit row."""
    samples: list[tuple[str, float]] = []
    for name in ("control.sense", "control.decide", "control.write", "control.audit"):
        recent = REGISTRY.recent(name, n=1)
        if recent:
            samples.append((name, recent[-1].duration_ms))
    if not samples:
        return None
    samples.sort(key=lambda x: x[1], reverse=True)
    return samples[0][0]
