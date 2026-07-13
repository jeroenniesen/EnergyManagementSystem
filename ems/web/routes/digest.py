"""Weekly digest routes + delivery (BACKLOG B-58 / roadmap P2 "the Sunday read").

GET /api/digest, plus the pieces the notify-loop in api.py still drives: `_run_weekly_digest`
(the Sunday-evening gate + dedupe + delivery, a plain testable function — api.py imports it) and
`gather_digest` (the shared week-window rollup both the route and the delivery job build on).
`_last_completed_week_monday` is the pure Mon-Sun boundary used by the default `/api/digest` view
(and unit-tested directly — api.py re-exports it).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ems.digest import build_digest
from ems.notify import Notifier
from ems.reporting import resolve_window
from ems.storage.cache import CacheStore
from ems.storage.history import HistoryStore
from ems.web.context import AppContext

_log = logging.getLogger("ems.web.digest")


def _last_completed_week_monday(now_local: datetime) -> date_cls:
    """The Monday of the most recently FULLY COMPLETED Mon-Sun week as of local `now_local`
    (BACKLOG B-58): always LAST week's Monday, even a few seconds after midnight on a Monday — the
    week that just started hasn't run yet, so it's never "the last completed week". Pure, so the
    Mon-Sun boundary is unit-testable without spinning up the app or faking a clock deep inside a
    closure."""
    this_monday = now_local.date() - timedelta(days=now_local.weekday())
    return this_monday - timedelta(days=7)


_DIGEST_CACHE_KEY = "digest:last_week_sent"
_DIGEST_CACHE_TTL_SECONDS = 32 * 24 * 3600.0  # comfortably longer than the weekly cadence


def _digest_title(saved_eur: float | None) -> str:
    if saved_eur is None:
        return "Your week"
    sign = "−" if saved_eur < 0 else ""
    return f"Your week: saved {sign}€{abs(saved_eur):.2f}"


async def _run_weekly_digest(
    store: HistoryStore | None,
    cache_store: CacheStore | None,
    notifier: Notifier | None,
    now: datetime,
    tz: ZoneInfo,
    gather: Callable[[date_cls], Awaitable[dict]],
) -> dict | None:
    """Sunday-evening delivery of the weekly digest (BACKLOG B-58 / roadmap P2 "the Sunday
    read"): once local time crosses Sunday 18:00, build + send the just-completed week's digest
    exactly once. Mirrors `_run_backup`'s shape — a plain, directly-testable function, NOT a
    closure — so the gate and the dedupe can be tested without running `_notify_loop`.

    GATE: only fires on a local Sunday at/after 18:00 — a mid-week restart, or a Sunday morning
    tick, never sends early.

    DEDUPE: the completed week's label is recorded in `cache_store` (`digest:last_week_sent`,
    PERSISTED so it survives a restart — unlike an in-memory box such as `_backup_state`, which
    only guards the same process); a week already recorded there is skipped WITHOUT calling
    `gather` again (no point re-assembling a digest nobody will see). The `Notifier`'s own
    `dedupe_key=f"digest:{week_label}"` is a second, independent safety net (see `ems/notify.py`'s
    module docstring) — belt and braces, so even a lost/cleared cache row can't double-send.

    `gather(monday)` does the actual I/O (finance/report/audit/advice for the week starting
    `monday`, then `build_digest`) — injected so this function stays a thin, testable gate +
    delivery wrapper, exactly like `_run_backup` delegates the real backup I/O to
    `store.backup_to`.

    Best-effort: any failure is logged and swallowed — a digest hiccup must never take down the
    notify loop (the same fail-safe convention as `_run_backup` / `Notifier.send`). Returns the
    digest dict that was actually sent, or None when it didn't fire (gate closed, dedupe, or no
    store/notifier configured)."""
    if store is None or notifier is None:
        return None
    now_local = now.astimezone(tz)
    if now_local.weekday() != 6 or now_local.hour < 18:  # 6 = Sunday
        return None
    # THIS week's Monday — the week ending TODAY (Sunday), not `_last_completed_week_monday`
    # (which is for /api/digest's default and deliberately looks back a full week, so a mid-week
    # browse never shows a still-changing window). The Sunday push is about the week just wrapping
    # up, accepting that its last few evening hours aren't in yet.
    monday = now_local.date() - timedelta(days=now_local.weekday())
    week_label = f"Week of {monday.isoformat()}"
    if cache_store is not None:
        try:
            already_sent = await asyncio.to_thread(cache_store.get, _DIGEST_CACHE_KEY)
        except Exception as exc:
            _log.debug("weekly digest dedupe cache read failed (non-fatal): %s", exc)
            already_sent = None
        if already_sent == week_label:
            return None
    try:
        digest = await gather(monday)
    except Exception:
        _log.exception("weekly digest gather failed; retry next cycle (fail-safe)")
        return None
    body = " ".join(x for x in (digest["headline"], digest.get("tweak")) if x).strip()
    await notifier.send(
        "weekly_digest", _digest_title(digest.get("saved_eur")), body,
        dedupe_key=f"digest:{week_label}",
    )
    if cache_store is not None:
        try:
            await asyncio.to_thread(
                cache_store.set, _DIGEST_CACHE_KEY, week_label, _DIGEST_CACHE_TTL_SECONDS)
        except Exception:
            _log.warning("weekly digest cache write failed (non-fatal)", exc_info=True)
    return digest


async def gather_digest(ctx: AppContext, anchor: date_cls) -> dict:
    """Gather everything `build_digest` needs for the week containing local date `anchor` and
    assemble it (BACKLOG B-58 / roadmap P2 "the Sunday read") — shared by `GET /api/digest`
    and the Sunday delivery job (`_run_weekly_digest`) so the notification and the on-demand
    read always agree for the same week.

    Finance rows only cover days up to "now" (a future/in-progress day has nothing to measure
    yet — `_ensure_day_finance` itself would refuse a future day); the week's flows/scores come
    from the SAME `_report_for_window` the Insights week view uses; the audit rows are the raw
    week-windowed audit log (`AuditStore.between`) build_digest counts patterns out of."""
    now_local = datetime.now(UTC).astimezone(ctx.site_tz)
    start, end, label, partial = resolve_window("week", anchor, ctx.site_tz, now_local)
    finance_rows: list[dict] = []
    if ctx.store is not None:
        cur = start
        while cur < end and cur <= now_local:
            finance_rows.append(await ctx.ensure_day_finance(cur.date()))
            cur += timedelta(days=1)
    report = await ctx.report_for_window("week", start, end, label, partial, now_local)
    audit_rows: list[dict] = []
    if ctx.audit_store is not None:
        audit_rows = await ctx.audit_store.between(
            start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat())
    advice = await ctx.solar_confidence_advice(datetime.now(UTC))
    export_model = str(ctx.settings_cache.get("prices.export_price_model", "net_metering"))
    return build_digest(
        finance_rows=finance_rows, flows=report["flows"], scores=report["scores"],
        audit_rows=audit_rows, advice=advice, week_label=label,
        export_price_model=export_model,
    )


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/digest")
    async def digest(week: str | None = None) -> dict:
        """The weekly digest (BACKLOG B-58 / roadmap P2 "Your week"): what you saved, what the
        system did, one suggested tweak — the same figures the Sunday-evening notification sends
        (see `_run_weekly_digest`), available on demand. Read-only. `week` (YYYY-MM-DD) is any
        date inside the desired week; omitted = the last COMPLETED Mon-Sun week (the current,
        still-running week is deliberately not the default — its numbers would keep changing)."""
        now_local = datetime.now(UTC).astimezone(ctx.site_tz)
        if week:
            try:
                anchor = date_cls.fromisoformat(week)
            except ValueError:
                return JSONResponse(  # type: ignore[return-value]
                    {"detail": "week must be YYYY-MM-DD"}, status_code=422)
        else:
            anchor = _last_completed_week_monday(now_local)
        return await gather_digest(ctx, anchor)

    return router
