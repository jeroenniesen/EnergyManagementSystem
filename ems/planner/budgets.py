"""Daily grid-charge budget (#317, audit F-03 / F-32).

`clamp_grid_charge` runs after a plan is built. It only removes grid-buying slots:
slots that would push the local day over `max_daily_grid_charge_kwh` become
`ALLOW_SELF_CONSUMPTION`. It never adds a charge slot and never writes to the battery.

The "already bought today" figure is recomputed from history for the site-local calendar
day (Europe/Amsterdam in production). It is not an in-memory counter, so a restart cannot
reset it early and local midnight — including 23 h and 25 h DST days — starts a new day.
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
_EPS = 1e-6


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


def slot_grid_kwh(slot: PlanSlot, per_slot_kwh: float) -> float:
    """Grid-charge energy a slot contributes to the daily cap.

    Prefer the slot's own `target_kwh` (what the planner scheduled). Otherwise the caller's
    per-slot quantum, then `power_w` over a 15-minute slot. Negative-price soak slots are
    ordinary `GRID_CHARGE_TO_TARGET` slots, so they count — a cap is a cap.
    """
    if slot.target_kwh is not None and slot.target_kwh > 0:
        return float(slot.target_kwh)
    if per_slot_kwh > 0:
        return float(per_slot_kwh)
    if slot.power_w is not None and slot.power_w > 0:
        return float(slot.power_w) * _SLOT_HOURS / 1000.0
    return 0.0


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
        total += slot_grid_kwh(slot, per_slot_kwh)
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

    ``already_bought_today_kwh is None`` means history could not be read: clamp this plan
    alone (treat already-bought as 0) and log a warning. Never refuse every charge slot
    just because history is missing.

    Slots are walked in plan order, per local calendar day. Today's budget is
    `cap − already bought`. Any other local day in the horizon gets a fresh `cap`
    (already-bought is today's counter only). Once a day is full, later charge slots
    that day become `ALLOW_SELF_CONSUMPTION` with `cap_reason`. When that removes
    buying and the plan already carries a buy-pool, its state becomes ``cap``.
    """
    zone = tz or _SITE_TZ
    cap = float(cap_kwh)
    if cap <= 0 or not plan.slots:
        return plan
    if already_bought_today_kwh is None:
        _log.warning(
            "daily grid-charge history unknown — clamping this plan only to %.3g kWh, "
            "not blocking charge",
            cap,
        )
        bought = 0.0
    else:
        bought = max(0.0, float(already_bought_today_kwh))

    today = _plan_day(plan, zone)
    reason = cap_reason(cap)
    used: dict[date, float] = defaultdict(float)
    capped: set[date] = set()
    blocked = False
    new_slots: list[PlanSlot] = []
    for slot in plan.slots:
        if slot.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
            new_slots.append(slot)
            continue
        day = slot.start.astimezone(zone).date()
        budget = cap - (bought if day == today else 0.0)
        if day in capped:
            blocked = True
            new_slots.append(_as_self_consumption(slot, reason))
            continue
        quantum = slot_grid_kwh(slot, per_slot_kwh)
        if used[day] + quantum <= budget + _EPS:
            used[day] += quantum
            new_slots.append(slot)
            continue
        capped.add(day)
        blocked = True
        new_slots.append(_as_self_consumption(slot, reason))

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
) -> float | None:
    """Integrate grid energy that charged the battery in `[day_start, day_end)`.

    `None` samples means history is unknown (caller must not treat that as a full cap).
    An empty list is a known zero. Sign convention matches history rollups: grid `+` is
    import, battery `−` is charge. A slot counts `min(charge, import)` so solar-only
    charging does not spend the grid cap, and a negative-price grid soak does.

    Each 15-minute bucket contributes mean power × 0.25 h, the same quantum as
    `aggregate_daily_energy`. Samples outside the window are ignored, which is what
    makes the counter roll over at local midnight.
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
    kwh = 0.0
    for bucket in batt:
        b = sum(batt[bucket]) / len(batt[bucket])
        g = sum(grid[bucket]) / len(grid[bucket])
        watts = min(max(0.0, -b), max(0.0, g))
        kwh += watts * _SLOT_HOURS / 1000.0
    return kwh


def read_already_bought_today(
    db_path: str | None,
    now: datetime,
    tz: ZoneInfo,
) -> float | None:
    """Read-only grid-charge kWh for the local day containing `now`.

    Returns `None` when the database cannot be read (unknown history). Returns `0.0`
    when the table exists and nothing was grid-charged in the window. No process memory:
    every call re-reads SQLite, so a restart reproduces the same total.
    """
    if not db_path:
        return None
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
    finally:
        conn.close()
    rows = [
        {"ts": ts, "grid_power_w": grid_w, "battery_power_w": batt_w}
        for ts, grid_w, batt_w in fetched
    ]
    return grid_charge_kwh_from_samples(rows, day_start=start, day_end=end, tz=tz)


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
