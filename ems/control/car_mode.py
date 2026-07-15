"""Home-battery behaviour WHILE THE CAR CHARGES — the pure decision core (feat/car-charge-modes).

Three behaviours, chosen by `control.car_charging_battery_mode` (see `ems.settings`):

* ``hold``            — today's car-guard, byte-for-byte: the battery idles so it can't discharge
                        into the car; solar + grid cover the car. The safe default.
* ``static_discharge``— the battery discharges at a fixed wattage. If that wattage is ABOVE the
                        actual house load, the surplus DOES flow to the car from the battery — a
                        deliberate choice the caller opts into (surfaced honestly in the reason).
* ``match_home_load`` — the battery discharges at the *predicted non-EV house load*, so it quietly
                        covers the house while the grid keeps feeding the car. The battery never
                        ends up charging the car, because it never exceeds the house's own draw.

Everything here is PURE: no clock, no I/O, no hardware. The caller (iteration 2) hands in the live
SoC, the settings, the predicted house load and the current battery setpoint; this module decides.

The anti-tracking contract
---------------------------
This must stay MODE-SWITCHING, not continuous control (CLAUDE.md / SPEC §2). `match_home_load` is
the tempting place to accidentally build a power-tracking loop that re-commands the battery every
cycle as the predicted load wiggles. It does NOT: `decide_car_mode_action` reports `recommand=True`
only at session start or when the new setpoint differs from the live one by more than
`rebond_threshold_w` (default 500 W). The caller commands the battery ONLY when `recommand` is set,
so a whole charging session costs a handful of writes, not one per cycle. This is the single rule
that keeps mode 3 honest — do not "helpfully" re-command on every small delta.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# --- tuning constants (documented; the caller passes the runtime-configurable ones as args) -------
_PREDICT_FLOOR_W = 150.0   # a lived-in house is never truly 0 W
_PREDICT_CAP_W = 3000.0    # sanity ceiling on a reconstructed non-EV load prediction
_SETPOINT_STEP_W = 50.0    # setpoint granularity — round predictions so noise doesn't re-command
_STATIC_FLOOR_W = 100.0    # smallest meaningful static discharge
_MATCH_FLOOR_W = 150.0     # match-mode never commands below the house floor

# The car-guard wording, kept in lock-step with `ems.web.api._car_guard` so mode "hold" is today's
# behaviour byte-for-byte.
_HOLD_REASON = (
    "car charging — holding the battery so it won't discharge into the car "
    "(solar + grid cover the car)"
)
_RESERVE_REASON = "battery at reserve — holding; grid covers the car and house"
_NOT_CHARGING_REASON = "car not charging — normal planning applies"

_DISCHARGE_MODES = ("static_discharge", "match_home_load")


@dataclass(frozen=True)
class CarModeAction:
    """What the battery should do this cycle while the car charges, and whether to (re-)command it.

    `action`   — ``"none"`` (car-mode dormant; the planner proceeds untouched), ``"hold"`` (idle,
                 the battery can't feed the car) or ``"discharge"`` (at `power_w`).
    `power_w`  — the discharge setpoint in W for ``"discharge"``; ``None`` otherwise.
    `reason`   — a human-readable, honest explanation (incl. when the battery deliberately feeds
                 the car), for the UI / audit / explainer.
    `recommand`— True only when the caller should actually issue the command this cycle (the bounded
                 re-command rule — see the module docstring). Holds carry True (applied every cycle,
                 idempotent downstream); a small discharge delta carries False (hold the setpoint).
    """

    action: str
    power_w: float | None
    reason: str
    recommand: bool


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _parse_utc(ts: object) -> datetime | None:
    """ISO-8601 → aware UTC datetime (naive ⇒ assumed UTC), or None if unparsable."""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _bucket(dt: datetime, now: datetime) -> tuple[int, int]:
    """The (weekday, hour) bucket of `dt`, in the SAME reference frame as `now` — so the match is
    self-consistent (both in UTC when the control loop passes a UTC `now`; both in the site tz when
    it passes a local one). ems.analysis-style weekday+hour bucketing (cf `load_baseline_error`)."""
    if now.tzinfo is not None:
        dt = dt.astimezone(now.tzinfo)
    else:  # naive `now` — bucket both in naive UTC
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return (dt.weekday(), dt.hour)


def _horizon_buckets(now: datetime, horizon_hours: float) -> set[tuple[int, int]]:
    """The set of (weekday, hour) buckets the half-open window [now, now+horizon) touches, stepping
    hour by hour in `now`'s own frame. Always includes `now`'s own bucket (a zero/short horizon)."""
    buckets = {(now.weekday(), now.hour)}
    end = now + timedelta(hours=horizon_hours)
    t = now
    while t < end:
        buckets.add((t.weekday(), t.hour))
        t = t + timedelta(hours=1)
    return buckets


def predict_house_load_w(
    observation_rows: list[dict], profile_expected_w: float, *,
    now: datetime, horizon_hours: float = 2.0,
) -> float:
    """The expected NON-EV house load (W) over roughly the next `horizon_hours`.

    Preference order:
      1. The trailing mean of `mean_non_ev_load_w` over observation rows that share the same
         (weekday, hour) bucket(s) as the window ahead — the household's own measured routine for
         this slice of the week (rows as `HistoryStore.observations_between` returns them).
      2. `profile_expected_w` — the load profile's expected value for now — when there is no such
         history yet.

    The result is floored at 150 W (a house is never truly 0), capped at 3000 W (sanity), and
    rounded to the nearest 50 W so ordinary sensor noise doesn't nudge the discharge setpoint and
    trigger a needless re-command. Pure — `now` is required (never reads a clock)."""
    targets = _horizon_buckets(now, horizon_hours)
    matched: list[float] = []
    for row in observation_rows:
        dt = _parse_utc(row.get("slot_start"))
        val = row.get("mean_non_ev_load_w")
        if dt is None or val is None:
            continue
        if _bucket(dt, now) in targets:
            try:
                matched.append(float(val))
            except (TypeError, ValueError):
                continue

    expected = (sum(matched) / len(matched)) if matched else float(profile_expected_w)
    clamped = _clamp(expected, _PREDICT_FLOOR_W, _PREDICT_CAP_W)
    return round(clamped / _SETPOINT_STEP_W) * _SETPOINT_STEP_W


def decide_car_mode_action(
    mode: str, *,
    car_charging: bool,
    soc_pct: float,
    min_reserve_soc: float,
    max_discharge_w: float,
    static_w: float,
    predicted_house_w: float,
    current_setpoint_w: float | None = None,
    rebond_threshold_w: float = 500.0,
) -> CarModeAction:
    """Decide the battery's behaviour while the car charges — the heart of the feature.

    `mode` is the resolved `control.car_charging_battery_mode`. The master toggle
    (`control.hold_battery_when_car_charging`) and "is the car actually charging?" detection are the
    CALLER's job; here `car_charging=False` is simply a no-op (`action="none"`, planner proceeds).

    The inviolable reserve floor: in either discharge mode, once SoC is within 1pp of
    `min_reserve_soc` the battery HOLDS instead — a 1pp hysteresis band so it doesn't flap at the
    boundary; the grid then covers both the car and the house.

    `recommand` gates whether the caller actually (re-)commands the battery this cycle — see the
    module docstring. It is True at session start (`current_setpoint_w is None`) or when the new
    discharge setpoint differs from the live one by more than `rebond_threshold_w`; holds carry True
    (applied each cycle, idempotent downstream)."""
    if not car_charging:
        return CarModeAction("none", None, _NOT_CHARGING_REASON, recommand=False)

    # "hold" (and any unknown/unsupported mode, defensively) = today's car-guard, applied per cycle.
    if mode not in _DISCHARGE_MODES:
        return CarModeAction("hold", None, _HOLD_REASON, recommand=True)

    # Inviolable reserve floor (both discharge modes), with a 1pp hysteresis band.
    if soc_pct <= min_reserve_soc + 1.0:
        return CarModeAction("hold", None, _RESERVE_REASON, recommand=True)

    if mode == "static_discharge":
        power = _clamp(static_w, _STATIC_FLOOR_W, max_discharge_w)
        if static_w > predicted_house_w:
            reason = (
                f"discharging {power:.0f} W — above the predicted house load "
                f"(~{predicted_house_w:.0f} W), so part of this feeds the car — your setting"
            )
        else:
            reason = f"discharging {power:.0f} W to cover the house while the grid feeds the car"
    else:  # match_home_load
        power = _clamp(predicted_house_w, _MATCH_FLOOR_W, max_discharge_w)
        reason = f"covering the house (~{power:.0f} W predicted) while the grid feeds the car"

    recommand = current_setpoint_w is None or abs(power - current_setpoint_w) > rebond_threshold_w
    return CarModeAction("discharge", power, reason, recommand=recommand)
