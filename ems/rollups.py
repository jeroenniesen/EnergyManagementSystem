"""Daily energy + finance rollup completeness (audit F-48 / #342).

No new money math. Decides which completed local days are due (last 7 only),
whether a day is still provisional, and which of those days are past noon
without a rollup. The scheduled rollup loop and finance/energy responses call
these helpers. Older history stays on the existing backfill tool.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from ems.storage.history import materialize_daily_energy, materialize_observations

if TYPE_CHECKING:
    from ems.storage.history import HistoryStore

_log = logging.getLogger(__name__)

LOOKBACK_DAYS = 7
PROVISIONAL_MIN_COVERAGE = 0.95
DEADLINE_HOUR = 12
ROLLUP_HOUR = 0
ROLLUP_MINUTE = 10

EnsureFinance = Callable[[date], Awaitable[object]]


def completed_lookback_days(
    now_local: datetime, *, lookback: int = LOOKBACK_DAYS,
) -> list[date]:
    """Completed local days in ``(today - lookback, today)``, oldest first.

    Today is never included. ``lookback`` is the hard cap (default 7): nothing
    older is a catch-up candidate.
    """
    if now_local.tzinfo is None:
        raise ValueError("now_local must be timezone-aware")
    today = now_local.date()
    first = today - timedelta(days=lookback)
    days: list[date] = []
    cursor = first
    while cursor < today:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def past_rollup_deadline(now_local: datetime, day: date) -> bool:
    """True at and after 12:00 local time on the day after ``day``."""
    if now_local.tzinfo is None:
        raise ValueError("now_local must be timezone-aware")
    deadline = datetime(
        day.year, day.month, day.day, DEADLINE_HOUR, tzinfo=now_local.tzinfo,
    ) + timedelta(days=1)
    return now_local >= deadline


def seconds_until_next_rollup(now_local: datetime) -> float:
    """Elapsed seconds to the next 00:10 in the site's local timezone.

    Convert both instants to UTC before subtraction: local days around a DST
    transition are 23 or 25 hours long, not always 24 hours.
    """
    if now_local.tzinfo is None:
        raise ValueError("now_local must be timezone-aware")
    target = datetime(
        now_local.year, now_local.month, now_local.day,
        ROLLUP_HOUR, ROLLUP_MINUTE, tzinfo=now_local.tzinfo,
    )
    if now_local >= target:
        target += timedelta(days=1)
    return (target.astimezone(UTC) - now_local.astimezone(UTC)).total_seconds()


def day_is_provisional(*, has_rollup: bool, sample_coverage: float | None) -> bool:
    """A day is provisional until it has a rollup and at least 95% of samples.

    ``sample_coverage`` is the 0..1 fraction already used by finance
    (``sample_coverage``) and the energy rollup (``daily_energy.coverage``).
    """
    if not has_rollup or sample_coverage is None:
        return True
    try:
        coverage = float(sample_coverage)
    except (TypeError, ValueError):
        return True
    return coverage < PROVISIONAL_MIN_COVERAGE


def apply_energy_provisional(
    payload: dict,
    *,
    period: str,
    day: str | None,
    coverage: dict[str, float | None],
) -> None:
    """Stamp ``provisional`` on a day report, or on each week/month day bucket.

    ``coverage`` maps local ``YYYY-MM-DD`` → ``daily_energy.coverage`` for days
    that already have an energy rollup. Absent keys have no rollup yet.
    Year buckets are months, so they are left unchanged.
    """
    if period == "day" and day is not None:
        payload["provisional"] = day_is_provisional(
            has_rollup=day in coverage, sample_coverage=coverage.get(day),
        )
        return
    if period not in ("week", "month"):
        return
    series = payload.get("series")
    if not isinstance(series, list):
        return
    for point in series:
        if not isinstance(point, dict):
            continue
        key = str(point.get("start") or "")[:10]
        point["provisional"] = day_is_provisional(
            has_rollup=key in coverage, sample_coverage=coverage.get(key),
        )


def _day_bounds(now_local: datetime, day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=now_local.tzinfo)
    end = start + timedelta(days=1)
    return start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()


def missing_rollup_days(db_path: str | None, now_local: datetime) -> list[date]:
    """Days in the lookback that are past noon, have meter samples, and lack a rollup.

    A rollup exists only when both ``daily_energy`` and ``daily_finance`` have a
    row. Days with no raw samples are not "missed jobs" (there was nothing to
    roll up). A read error yields an empty list so a locked DB cannot raise a
    false alert. Read-only; safe to call from the sync alerts endpoint.
    """
    if not db_path or now_local.tzinfo is None:
        return []
    due = [d for d in completed_lookback_days(now_local) if past_rollup_deadline(now_local, d)]
    if not due:
        return []
    try:
        conn = sqlite3.connect(db_path, timeout=1.0)
    except sqlite3.Error:
        _log.debug("rollup lookup failed to open %s", db_path, exc_info=True)
        return []
    try:
        conn.row_factory = sqlite3.Row
        missing: list[date] = []
        for day in due:
            key = day.isoformat()
            start_iso, end_iso = _day_bounds(now_local, day)
            try:
                sampled = conn.execute(
                    "SELECT 1 FROM raw_samples WHERE ts >= ? AND ts < ? LIMIT 1",
                    (start_iso, end_iso),
                ).fetchone()
            except sqlite3.Error:
                return []
            if sampled is None:
                continue
            try:
                energy = conn.execute(
                    "SELECT 1 FROM daily_energy WHERE date = ? LIMIT 1", (key,),
                ).fetchone()
                finance = conn.execute(
                    "SELECT 1 FROM daily_finance WHERE day = ? LIMIT 1", (key,),
                ).fetchone()
            except sqlite3.Error:
                return []
            if energy is None or finance is None:
                missing.append(day)
        return missing
    finally:
        conn.close()


async def catch_up_rollups(
    store: HistoryStore,
    now_local: datetime,
    *,
    cadence_seconds: float,
    ensure_finance: EnsureFinance,
) -> list[date]:
    """Fill missing energy + finance rollups for the last 7 completed local days.

    Idempotent. Yesterday is refreshed when raw samples still exist (late
    samples). Older days are written only when the row is absent. A day with
    no raw samples is left untouched, so an existing rollup is never replaced
    with empty data and a day that never recorded anything stays missing.
    Returns the dates that newly gained an energy rollup.
    """
    days = completed_lookback_days(now_local)
    if not days:
        return []
    start_key = days[0].isoformat()
    end_key = (days[-1] + timedelta(days=1)).isoformat()
    energy_rows = await store.daily_energy_between(start_key, end_key)
    finance_rows = await store.daily_finance_between(start_key, end_key)
    energy_days = {row["date"] for row in energy_rows}
    finance_days = {row["day"] for row in finance_rows}
    yesterday = now_local.date() - timedelta(days=1)
    filled: list[date] = []
    for day in days:
        start = datetime(day.year, day.month, day.day, tzinfo=now_local.tzinfo)
        end = start + timedelta(days=1)
        raw = await store.raw_between(
            start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat(), limit=1,
        )
        has_samples = bool(raw)
        key = day.isoformat()
        if has_samples and (key not in energy_days or day == yesterday):
            await materialize_observations(store, start, end, cadence_seconds=cadence_seconds)
            await materialize_daily_energy(store, start, end, cadence_seconds=cadence_seconds)
            if key not in energy_days:
                filled.append(day)
                energy_days.add(key)
        # Finance: fill gaps and refresh yesterday; #131 path when raw is gone but cache remains.
        need_finance = (
            has_samples and (key not in finance_days or day == yesterday)
        ) or (key in finance_days and not has_samples)
        if need_finance:
            await ensure_finance(day)
    return filled
