"""Daily grid-charge budget (#317, audit F-03 / F-32).

`clamp_grid_charge` runs after a plan is built. It only removes grid-buying slots:
slots that would push the local day over `max_daily_grid_charge_kwh` become
`ALLOW_SELF_CONSUMPTION`. It never adds a charge slot and never writes to the battery.

Energy is grid energy (AC kWh bought), not DC energy stored in the pack. The "already
bought today" figure is recomputed from history for the site-local calendar day
(Europe/Amsterdam in production). It is not an in-memory counter, so a restart cannot
reset it early and local midnight — including 23 h and 25 h DST days — starts a new day.
A missing counter (`None`) means today's cap is already reached: no further grid charging.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.schedule import Plan, PlanSlot
from ems.timeutil import require_aware, slot_start

_log = logging.getLogger("ems.planner.budgets")

_SITE_TZ = ZoneInfo("Europe/Amsterdam")
_SLOT_HOURS = 0.25
_SLOT = timedelta(minutes=15)
_EPS = 1e-6
# PO #317: unknown counter (DB error) → no grid charging. Discharge / self-use stay.
UNKNOWN_COUNTER_REASON = "Dagteller onbekend: niet laden van het net"
_GRID_CHARGE_INTENTS = frozenset({"grid_charge_to_target", "GRID_CHARGE_TO_TARGET"})


def cap_reason(cap_kwh: float) -> str:
    """Homeowner-facing reason on every slot the daily cap drops."""
    shown = float(cap_kwh)
    text = str(int(shown)) if abs(shown - round(shown)) < 1e-9 else f"{shown:g}"
    return f"Dagplafond {text} kWh bereikt"


def local_day_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """`[local midnight, next local midnight)` in `tz`.

    Next midnight is built from the calendar date, not by adding 24 h. Subtracting two
    aware datetimes that share a tzinfo is wall-clock arithmetic and would hide the 23 h
    spring day and the 25 h autumn day. Compare the bounds in UTC for the true span.
    """
    require_aware(now, "now")
    local = now.astimezone(tz)
    start = datetime(local.year, local.month, local.day, tzinfo=tz)
    nxt = local.date() + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start, end


def ac_grid_kwh_per_slot(max_charge_w: float) -> float:
    """AC kWh a full-power charge slot buys from the grid.

    `max_charge_w * 0.25 h`, without the charge-side efficiency. `target_kwh` on a slot is
    DC energy stored in the pack (`power * time * √η`) and is about 5% smaller — the cap is
    a grid-purchase ceiling, so it must not be accounted in that smaller unit.
    """
    return max(0.0, float(max_charge_w)) * _SLOT_HOURS / 1000.0


def _remaining_fraction(start: datetime, now: datetime) -> float:
    """Share of a 15-minute slot that is still ahead of `now` (1 = not started, 0 = finished).

    The elapsed part is already in the history counter. Counting the whole slot again in the
    plan would buy the same minutes twice. Arithmetic is on UTC instants so a DST fold does
    not shrink or stretch the quarter-hour.
    """
    start_utc = start.astimezone(UTC)
    now_utc = now.astimezone(UTC)
    end_utc = start_utc + _SLOT
    span = (end_utc - start_utc).total_seconds()
    if span <= 0 or now_utc <= start_utc:
        return 1.0
    if now_utc >= end_utc:
        return 0.0
    return max(0.0, min(1.0, (end_utc - now_utc).total_seconds() / span))


def slot_grid_kwh(
    slot: PlanSlot,
    per_slot_kwh: float,
    *,
    now: datetime | None = None,
) -> float:
    """AC grid-charge kWh this slot contributes to the daily cap.

    `power_w` over a quarter-hour is the grid purchase. `per_slot_kwh` is the same AC quantum
    when the slot has no power (callers pass `ac_grid_kwh_per_slot`). `target_kwh` is DC stored
    energy and is not the cap unit. Negative-price soak slots are ordinary
    `GRID_CHARGE_TO_TARGET` slots, so they count — a cap is a cap.

    When `now` falls inside the slot, only the remaining fraction counts. The elapsed fraction
    is the history counter's share of that same slot.
    """
    if slot.power_w is not None and slot.power_w > 0:
        full = float(slot.power_w) * _SLOT_HOURS / 1000.0
    elif per_slot_kwh > 0:
        full = float(per_slot_kwh)
    else:
        return 0.0
    if now is None:
        return full
    return full * _remaining_fraction(slot.start, now)


def planned_grid_charge_kwh(
    plan: Plan,
    per_slot_kwh: float = 0.0,
    *,
    tz: ZoneInfo | None = None,
    day: date | None = None,
) -> float:
    """Sum of grid-charge kWh on `day` (default: the plan's local created-at day)."""
    zone = tz or _SITE_TZ
    if day is None:
        day = _plan_day(plan, zone)
    total = 0.0
    for slot in plan.slots:
        if slot.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
            continue
        if slot.start.astimezone(zone).date() != day:
            continue
        total += slot_grid_kwh(slot, per_slot_kwh, now=plan.created_at)
    return total


def clamp_grid_charge(
    plan: Plan,
    already_bought_today_kwh: float | None,
    cap_kwh: float,
    per_slot_kwh: float,
    *,
    tz: ZoneInfo | None = None,
) -> Plan:
    """Drop grid-charge slots that would exceed the local-day cap.

    ``already_bought_today_kwh is None`` means the counter could not be read. Today's cap is
    treated as already reached: every grid-charge slot today becomes self-consumption with
    `UNKNOWN_COUNTER_REASON`. Later local days still get a fresh cap (the counter is today's).
    Discharge and self-consumption slots are left as they are.

    Slots are walked in plan order, per local calendar day. Today's budget is
    `cap − already bought`. Any other local day in the horizon gets a fresh `cap`
    (already-bought is today's counter only). Once a day is full, later charge slots
    that day become `ALLOW_SELF_CONSUMPTION` with `cap_reason`. The running slot counts
    only its remaining fraction, so the elapsed part (already in the counter) is not
    bought again. When clamping removes buying and the plan already carries a buy-pool,
    its state becomes ``cap``.
    """
    zone = tz or _SITE_TZ
    cap = float(cap_kwh)
    if cap <= 0 or not plan.slots:
        return plan
    unknown = already_bought_today_kwh is None
    if unknown:
        _log.warning(
            "daily grid-charge counter unknown — not charging from the grid (cap %.3g kWh "
            "treated as reached)",
            cap,
        )
        bought = cap
    else:
        bought = max(0.0, float(already_bought_today_kwh))

    today = _plan_day(plan, zone)
    reason = cap_reason(cap)
    now = plan.created_at
    used: dict[date, float] = defaultdict(float)
    capped: set[date] = set()
    blocked = False
    new_slots: list[PlanSlot] = []
    for slot in plan.slots:
        if slot.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
            new_slots.append(slot)
            continue
        day = slot.start.astimezone(zone).date()
        slot_reason = UNKNOWN_COUNTER_REASON if unknown and day == today else reason
        budget = cap - (bought if day == today else 0.0)
        if day in capped or (unknown and day == today):
            blocked = True
            new_slots.append(_as_self_consumption(slot, slot_reason))
            continue
        quantum = slot_grid_kwh(slot, per_slot_kwh, now=now)
        if used[day] + quantum <= budget + _EPS:
            used[day] += quantum
            new_slots.append(slot)
            continue
        capped.add(day)
        blocked = True
        new_slots.append(_as_self_consumption(slot, slot_reason))

    if not blocked:
        return plan
    buy_pool = plan.buy_pool
    if buy_pool is not None and buy_pool.state != "cap":
        buy_pool = replace(buy_pool, state="cap")
    return replace(plan, slots=tuple(new_slots), buy_pool=buy_pool)


def grid_charge_kwh_from_samples(
    samples: list[dict] | None,
    *,
    day_start: datetime,
    day_end: datetime,
    tz: ZoneInfo,
    now: datetime | None = None,
    planned_charge_w_by_slot: dict[datetime, float] | None = None,
) -> float | None:
    """Integrate grid energy that charged the battery in `[day_start, day_end)`.

    `None` samples means history is unknown (the caller treats the cap as reached).
    An empty list is a known zero. Sign convention matches history rollups: grid `+` is
    import, battery `−` is charge. A measured slot counts `min(charge, import)` so
    solar-only charging does not spend the grid cap, and a negative-price grid soak does.

    A finished 15-minute bucket with samples contributes mean power × 0.25 h. The bucket
    that contains `now` contributes mean power × elapsed hours only (pro rata): the rest
    of that slot is still in the plan, and counting both in full would buy it twice.
    Buckets that have not started yet are not "already bought".

    A bucket with no samples is a measurement gap. If `planned_charge_w_by_slot` marks it
    as a GRID_CHARGE slot, it counts as that planned power for the whole slot (or the
    elapsed fraction, when it is the running slot). A gap that is not a charge slot
    counts as 0. Keys are UTC slot starts, the same key as the sample buckets.
    """
    if samples is None:
        return None
    batt: dict[datetime, list[float]] = defaultdict(list)
    grid: dict[datetime, list[float]] = defaultdict(list)
    for row in samples:
        ts = _parse_ts(row.get("ts"))
        if ts is None or not (day_start <= ts < day_end):
            continue
        # Key in UTC so the repeated autumn hour (fold=0 and fold=1, equal as wall
        # time) stays two buckets. Aware subtraction that shares a tzinfo ignores DST.
        bucket = slot_start(ts, tz).astimezone(UTC)
        batt[bucket].append(float(row.get("battery_power_w") or 0.0))
        grid[bucket].append(float(row.get("grid_power_w") or 0.0))
    planned = planned_charge_w_by_slot or {}
    start_utc = day_start.astimezone(UTC)
    end_utc = day_end.astimezone(UTC)
    now_utc = now.astimezone(UTC) if now is not None else None
    keys = set(batt) | {b for b in planned if start_utc <= b < end_utc}
    kwh = 0.0
    for bucket in keys:
        if not (start_utc <= bucket < end_utc):
            continue
        end_bucket = bucket + _SLOT
        if now_utc is not None and bucket >= now_utc:
            continue  # slot has not started; the plan still owns it
        if now_utc is not None and bucket <= now_utc < end_bucket:
            hours = (now_utc - bucket).total_seconds() / 3600.0
        else:
            hours = _SLOT_HOURS
        if bucket in batt:
            b = sum(batt[bucket]) / len(batt[bucket])
            g = sum(grid[bucket]) / len(grid[bucket])
            watts = min(max(0.0, -b), max(0.0, g))
        else:
            watts = planned.get(bucket) or 0.0
            if watts <= 0:
                continue
        kwh += max(0.0, watts) * hours / 1000.0
    return kwh


def read_already_bought_today(
    db_path: str | None,
    now: datetime,
    tz: ZoneInfo,
    *,
    planned_charge_w: float | None = None,
) -> float | None:
    """Read-only grid-charge kWh for the local day containing `now`.

    Returns `None` when the database cannot be read (unknown counter → no grid charging).
    Returns `0.0` when the table exists and nothing was grid-charged in the window.
    No process memory: every call re-reads SQLite, so a restart reproduces the same total.

    `planned_charge_w` (the battery's charge power) fills measurement gaps: a
    `plan_history` row whose intent was grid-charge counts as a full slot at that power
    when the slot has no samples, and as 0 elapsed power when the slot is still running.
    A missing `plan_history` table does not make the counter unknown — gaps then count
    as 0. The window is `[local midnight, next local midnight)`, so a sample timestamped
    exactly 00:00:00 is today's and the next midnight is not.
    """
    if not db_path:
        return None
    require_aware(now, "now")
    start, end = local_day_bounds(now, tz)
    start_iso = start.astimezone(UTC).isoformat()
    end_iso = end.astimezone(UTC).isoformat()
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        try:
            cur = conn.execute(
                "SELECT ts, grid_power_w, battery_power_w FROM raw_samples "
                "WHERE ts >= ? AND ts < ?",
                (start_iso, end_iso),
            )
            fetched = cur.fetchall()
        except sqlite3.Error:
            return None
        planned: dict[datetime, float] = {}
        if planned_charge_w is not None and planned_charge_w > 0:
            try:
                intent_rows = conn.execute(
                    "SELECT ts, intent FROM plan_history WHERE ts >= ? AND ts < ?",
                    (start_iso, end_iso),
                ).fetchall()
            except sqlite3.Error:
                intent_rows = []
            for ts, intent in intent_rows:
                if str(intent) not in _GRID_CHARGE_INTENTS:
                    continue
                parsed = _parse_ts(ts)
                if parsed is None or not (start <= parsed < end):
                    continue
                planned[slot_start(parsed, tz).astimezone(UTC)] = float(planned_charge_w)
    finally:
        conn.close()
    rows = [
        {"ts": ts, "grid_power_w": grid_w, "battery_power_w": batt_w}
        for ts, grid_w, batt_w in fetched
    ]
    return grid_charge_kwh_from_samples(
        rows, day_start=start, day_end=end, tz=tz, now=now,
        planned_charge_w_by_slot=planned,
    )


def _plan_day(plan: Plan, tz: ZoneInfo) -> date:
    created = plan.created_at
    require_aware(created, "plan.created_at")
    return created.astimezone(tz).date()


def _as_self_consumption(slot: PlanSlot, reason: str) -> PlanSlot:
    return replace(
        slot,
        intent=BatteryIntent.ALLOW_SELF_CONSUMPTION,
        reason=reason,
        target_kwh=None,
        power_w=None,
    )


def _parse_ts(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
