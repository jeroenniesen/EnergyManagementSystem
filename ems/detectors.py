"""Forecast-driven notification detectors (BACKLOG B-75): notify only for predicted MEANINGFUL
events, sparse and confidence-backed, through the Notifier rails (`ems/notify.py`, B-20) that
`_run_backup` already proved end-to-end (`ems/web/api.py`).

Each detector below is PURE — plain data in, no clock reads, no I/O — and returns either `None`
("nothing to say right now") or a dict shaped exactly like `Notifier.send()`'s keyword arguments::

    {"key": str, "title": str, "body": str, "confidence": str, "dedupe_key": str}

so the caller can always do ``if (r := detector(...)) is not None: await notifier.send(**r)``.
Bodies follow the B-37 calm style (what happened + what EMS does + what you can do), kept to
one short sentence (well under the 200-char budget) — see each function's literal template.

`now` is always supplied by the caller and is assumed to be a LOCAL, tz-aware datetime (site
timezone) — the evening-window gate and the "tomorrow" calendar date both need local wall time,
and a pure function must not read the clock or a timezone itself.

None of these functions guess at missing data: an empty/absent forecast, plan, or baseline reads
as "nothing to say", never as a worst-case assumption — a false "grey day" or "cheap window"
notification born from missing data would be worse than staying quiet (CLAUDE.md fail-safe).

`typical_daily_solar_kwh` is NOT a detector — it is the caller-side helper that prepares
`low_solar_tomorrow`'s baseline argument (median of the last 14 days' actual solar from raw
history rows), kept here because it is pure and most naturally tested alongside its consumer.

`device_unreachable` (issue #128) is also pure aside from mutating a caller-owned
`DeviceUnreachableState`; it never writes to the battery. Open incidents survive EMS restart via
`open_incidents_from_notifications` over the existing `notifications` table.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ems.retrospect import _floor, _mean, _parse

SLOT = timedelta(minutes=15)
_SLOT_HOURS = 0.25

# Shared once-a-day evening heads-up window for the two "look ahead to tomorrow" detectors
# (low_solar_tomorrow, price_opportunity): early evening is when a "tomorrow" heads-up is both
# fresh news (day-ahead prices/forecasts have just settled) and still actionable (time to plan
# the dishwasher, or simply not be surprised by a grey day) — before it, and long after it,
# repeating the same message would just be noise. Half-open so a cycle landing exactly on
# the boundary behaves deterministically.
_EVENING_START = time(17, 0)
_EVENING_END = time(21, 0)


def _in_evening_window(now: datetime) -> bool:
    t = now.time()
    return _EVENING_START <= t < _EVENING_END


def low_solar_tomorrow(
    p50_by_slot_tomorrow: dict[datetime, float],
    typical_daily_kwh: float | None,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """"Grey day tomorrow" heads-up: tomorrow's forecast solar (P50, summed to kWh) is under 40%
    of the recent typical daily yield. `typical_daily_kwh` is computed by the CALLER (see
    `typical_daily_solar_kwh` below) — this function never estimates a baseline itself. Fires
    only in the 17:00-21:00 local evening window (once a day, not a running nag); never on an
    empty forecast or a missing/non-positive baseline (nothing to compare against yet)."""
    if not p50_by_slot_tomorrow:
        return None
    if typical_daily_kwh is None or typical_daily_kwh <= 0:
        return None
    if not _in_evening_window(now):
        return None

    tomorrow_kwh = sum(w * _SLOT_HOURS / 1000.0 for w in p50_by_slot_tomorrow.values())
    if tomorrow_kwh >= 0.4 * typical_daily_kwh:
        return None

    tomorrow = now.date() + timedelta(days=1)
    return {
        "key": "low_solar_tomorrow",
        "title": "Grey day tomorrow",
        "body": (
            f"~{tomorrow_kwh:.1f} kWh forecast vs your usual {typical_daily_kwh:.1f} kWh — "
            "EMS will lean on cheap grid windows overnight instead of solar."
        ),
        "confidence": "medium",
        "dedupe_key": f"low_solar:{tomorrow.isoformat()}",
    }


def ev_plug_in_reminder(
    car_plan: dict[str, Any] | None,
    car_charging_now: bool,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Reminder to plug the car in: the cheapest planned charge window (the earliest entry of
    `car_plan["windows"]`, from `ems.ev_planner.plan_car_charging` — its windows are chronological
    and never start in the past) starts within the next 3 hours, and the car isn't already
    charging. `car_plan` is the PLAN sub-dict, not the full `/api/car/plan` response envelope;
    `None`/no windows (EV advice off, no anchor/schedule, or nothing left to charge for) is
    silently "nothing to remind about"."""
    if car_charging_now or not car_plan:
        return None
    windows = car_plan.get("windows") or []
    if not windows:
        return None

    w = windows[0]
    start = datetime.fromisoformat(w["start"])
    delta = start - now
    if delta < timedelta(0) or delta > timedelta(hours=3):
        return None

    kwh = float(w.get("battery_kwh", 0.0))
    return {
        "key": "ev_plug_in",
        "title": "Plug in the car soon",
        "body": (
            f"Cheapest charge window starts at {start:%H:%M} ({kwh:.1f} kWh planned). "
            "Plug the car in before then."
        ),
        "confidence": "high",
        "dedupe_key": f"ev_plug_in:{w['start']}",
    }


def evening_peak_risk(
    projected_soc_at_peak: float | None,
    needed_soc: float | None,
    confidence_level: str | None,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Heads-up that the CURRENT plan's projected SoC at tonight's peak/deadline is more than 5
    percentage points under what's needed to cover it — a forward risk, not yet a fact. Gated on
    `confidence_level` (the same "high"/"medium"/"low" from `ems.confidence.plan_confidence`)
    NOT being "low": a low-confidence projection is already unreliable, so alarming on top of it
    would just be noise on noise (CLAUDE.md fail-safe — stay quiet when uncertain)."""
    if projected_soc_at_peak is None or needed_soc is None or confidence_level is None:
        return None
    if confidence_level == "low":
        return None
    if projected_soc_at_peak >= needed_soc - 5.0:
        return None

    return {
        "key": "peak_risk",
        "title": "Battery may fall short tonight",
        "body": (
            f"Battery may fall short of tonight's peak (projected {projected_soc_at_peak:.0f}% "
            f"vs {needed_soc:.0f}% needed). Check tonight's reserve advice on the dashboard — "
            f"EMS will top up if the price allows."
        ),
        "confidence": confidence_level,
        "dedupe_key": f"peak_risk:{now.date().isoformat()}",
    }


def _is_cheap(price: float, avg: float) -> bool:
    """A slot counts as "unusually cheap" if it is negative, or under 30% of the day's average
    (guarded — the 30% test is meaningless once the average itself isn't positive)."""
    return price < 0 or (avg > 0 and price < 0.3 * avg)


def price_opportunity(
    price_slots_tomorrow: list[Any],
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Heads-up when tomorrow's day-ahead prices show a genuinely cheap window: any negative-price
    slot, OR a minimum price under 30% of tomorrow's average (equivalent to "any slot is
    `_is_cheap`" — if the cheapest slot doesn't qualify, none do). Reports the cheapest CONTIGUOUS
    run of qualifying slots (ties broken by earliest start). `price_slots_tomorrow` items need
    only `.start`/`.eur_per_kwh` (duck-typed like `ems.sources.prices.PriceSlot`). Evening
    evaluation window like `low_solar_tomorrow` — a look-ahead-to-tomorrow heads-up, not a
    running nag."""
    if not price_slots_tomorrow:
        return None
    if not _in_evening_window(now):
        return None

    slots = sorted(price_slots_tomorrow, key=lambda s: s.start)
    avg = _mean([s.eur_per_kwh for s in slots])
    if not any(_is_cheap(s.eur_per_kwh, avg) for s in slots):
        return None

    runs: list[list[Any]] = []
    for s in slots:
        if not _is_cheap(s.eur_per_kwh, avg):
            continue
        if runs and s.start - runs[-1][-1].start == SLOT:
            runs[-1].append(s)
        else:
            runs.append([s])
    runs.sort(key=lambda run: _mean([s.eur_per_kwh for s in run]))  # cheapest run first

    run = runs[0]
    start, end = run[0].start, run[-1].start + SLOT
    price = _mean([s.eur_per_kwh for s in run])
    tomorrow = now.date() + timedelta(days=1)
    return {
        "key": "price_opportunity",
        "title": "Unusually cheap power tomorrow",
        "body": (
            f"Unusually cheap power tomorrow {start:%H:%M}–{end:%H:%M} "
            f"(€{price:.2f}/kWh). Good moment for the dishwasher/laundry — "
            "EMS handles the battery."
        ),
        "confidence": "high",
        "dedupe_key": f"price_opp:{tomorrow.isoformat()}",
    }


def typical_daily_solar_kwh(
    raw_rows: list[dict],
    tz: ZoneInfo,
    today: date,
    *,
    days: int = 14,
) -> float | None:
    """Median daily solar production (kWh) over the `days` most recently COMPLETED local calendar
    days — from `today - days` through YESTERDAY; `today` itself is always excluded, since a
    partial day would understate it. `raw_rows` are plain dicts with `ts` (UTC-ISO) and
    `solar_power_w`, e.g. straight from `HistoryStore.raw_between`.

    Each row is bucketed into its 15-minute slot (mean W per slot) before integrating to kWh —
    mirroring `ems.reporting.build_series` — so an irregular sampling cadence can't double- or
    under-count. Returns `None` when no day in the window has any data at all; callers must treat
    that as "not enough history yet", never silently as zero."""
    watts_by_slot: dict[datetime, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None:
            continue
        watts_by_slot[_floor(dt)].append(float(r.get("solar_power_w", 0.0)))

    earliest = today - timedelta(days=days)
    kwh_by_day: dict[date, float] = defaultdict(float)
    for slot, watts in watts_by_slot.items():
        local_day = slot.astimezone(tz).date()
        if earliest <= local_day < today:
            kwh_by_day[local_day] += _mean(watts) * _SLOT_HOURS / 1000.0

    if not kwh_by_day:
        return None
    values = sorted(kwh_by_day.values())
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2.0


# ---------------------------------------------------------------------------------------------
# Device unreachable (issue #128) — P1 meter / battery silence → sparse push; notify only.
# ---------------------------------------------------------------------------------------------

# Default: alert after 15 min without a fresh reading; clear the incident only after 10 min of
# unbroken contact so short flaps don't open a new outage or spam recovery pushes.
DEFAULT_UNREACHABLE_THRESHOLD = timedelta(minutes=15)
STABLE_RECOVER_AFTER = timedelta(minutes=10)

_DEVICE_LABEL = {
    "battery": "Batterij",
    "p1": "P1-meter",
}


@dataclass
class DeviceUnreachableState:
    """Per-device outage tracker. `incident_since` is the wall-clock moment we first declared the
    device down for THIS outage — deliberately NOT derived from `last_fresh_at` (a short flap
    would shift that timestamp and look like a new incident). Cleared only after
    `STABLE_RECOVER_AFTER` of continuous contact. `down_push_sent` gates the recovery push."""

    incident_since: datetime | None = None
    down_push_sent: bool = False
    contact_since: datetime | None = None  # set while recovering; cleared on re-drop


def _hhmm(ts: datetime) -> str:
    return f"{ts.hour:02d}:{ts.minute:02d}"


def _down_key(device: str) -> str:
    return f"{device}_down"


def _up_key(device: str) -> str:
    return f"{device}_up"


def _dedupe(device: str, kind: str, incident_since: datetime) -> str:
    return f"{device}_{kind}:{incident_since.isoformat()}"


def device_unreachable(
    device: str,
    last_fresh_at: datetime | None,
    *,
    now: datetime,
    state: DeviceUnreachableState,
    threshold: timedelta = DEFAULT_UNREACHABLE_THRESHOLD,
    stable_recover: timedelta = STABLE_RECOVER_AFTER,
    last_known_stand: str | None = None,
    stand_confirmed: bool = True,
    dry_run: bool = True,
) -> dict[str, Any] | None:
    """Push when P1 or battery has had no fresh reading for `threshold` (default 15 min).

    Pure except for mutating `state` (caller owns the per-device dict across cycles). Returns a
    `Notifier.send` kwargs dict or None. Never opens a battery write path.

    Rules (issue #128):
    - Never-seen (`last_fresh_at is None`) stays quiet until an incident is already open
      (e.g. restored after restart) — avoids a boot-time false alarm before the first sample.
    - `incident_since` sticks across short flaps; only clears after `stable_recover` of contact.
    - At most one down push per incident (`dedupe_key` embeds `incident_since`); recovery push
      only when that down push was actually sent (`state.down_push_sent`).
    - Unconfirmed last write → "Laatst bekende stand: onbekend" with no mode name.
    """
    if device not in _DEVICE_LABEL:
        return None

    is_down = (
        last_fresh_at is not None
        and (now - last_fresh_at) >= threshold
    )
    # Restored open incident with still-missing data also counts as down.
    if last_fresh_at is None and state.incident_since is not None:
        is_down = True

    label = _DEVICE_LABEL[device]
    threshold_min = max(1, int(threshold.total_seconds() // 60))

    if is_down:
        state.contact_since = None
        if state.incident_since is None:
            state.incident_since = now
        # Always re-emit the same down payload; Notifier dedupe suppresses repeats.
        sinds = _hhmm(last_fresh_at) if last_fresh_at is not None else _hhmm(state.incident_since)
        if stand_confirmed and last_known_stand:
            stand_txt = last_known_stand
        else:
            stand_txt = "onbekend"
        result = {
            "key": _down_key(device),
            "title": f"{label} reageert niet",
            "body": (
                f"Al {threshold_min} minuten geen contact (sinds {sinds}). "
                f"Laatst bekende stand: {stand_txt}. "
                "Kijk of het apparaat stroom en netwerk heeft."
            ),
            "confidence": "high",
            "dedupe_key": _dedupe(device, "down", state.incident_since),
        }
        # Mark intent to notify; wiring may clear this if send() fails before store.
        state.down_push_sent = True
        return result

    # Contact is back (or never lost).
    if state.incident_since is None:
        return None

    if state.contact_since is None:
        state.contact_since = now

    if (now - state.contact_since) < stable_recover:
        return None  # flap window — keep the incident open, no recovery yet

    # Stable recovery.
    incident = state.incident_since
    sent = state.down_push_sent
    state.incident_since = None
    state.down_push_sent = False
    state.contact_since = None
    if not sent:
        return None  # never pushed the down alert → no recovery noise

    verb = "kijkt weer mee" if dry_run else "stuurt weer"
    return {
        "key": _up_key(device),
        "title": f"{label} weer bereikbaar",
        "body": f"{label} weer bereikbaar, EMS {verb}.",
        "confidence": "high",
        "dedupe_key": _dedupe(device, "up", incident),
    }


def open_incidents_from_notifications(
    rows: list[dict[str, Any]],
) -> dict[str, DeviceUnreachableState]:
    """Rebuild open per-device incidents from the notifications table (restart survival).

    An incident is open when a `*_down:<iso>` has no matching `*_up:<iso>` with the same
    incident timestamp. No new table — HistoryStore only.
    """
    downs: dict[str, set[datetime]] = defaultdict(set)
    ups: dict[str, set[datetime]] = defaultdict(set)
    for row in rows:
        key = str(row.get("key") or "")
        dk = row.get("dedupe_key")
        if key.endswith("_down"):
            device, kind = key[: -len("_down")], "down"
        elif key.endswith("_up"):
            device, kind = key[: -len("_up")], "up"
        else:
            continue
        if device not in _DEVICE_LABEL:
            continue
        iso = None
        if isinstance(dk, str) and ":" in dk:
            iso = dk.split(":", 1)[1]
        if not iso:
            continue
        try:
            ts = datetime.fromisoformat(iso)
        except ValueError:
            continue
        (downs if kind == "down" else ups)[device].add(ts)

    out: dict[str, DeviceUnreachableState] = {}
    for device, since_set in downs.items():
        open_set = since_set - ups.get(device, set())
        if not open_set:
            continue
        since = max(open_set)
        out[device] = DeviceUnreachableState(
            incident_since=since, down_push_sent=True, contact_since=None,
        )
    return out
