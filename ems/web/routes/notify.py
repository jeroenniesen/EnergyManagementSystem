"""Notification-outbox routes (BACKLOG B-20 slice, extracted from create_app).

GET /api/notifications (the header-bell feed) · POST /api/notifications/read.

AUTH: POST /api/notifications/read is a write; it is gated centrally by `_AccessMiddleware` in
api.py, whose `_WRITE_API_PATHS` set includes "/api/notifications/read". Moving the handler here
does NOT change its path, so the middleware still guards it — keep the path in that set if this
route is ever renamed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ems.web.context import AppContext


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/notifications")
    async def notifications_endpoint(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        """The outbox feed for the header bell (B-20): most recent notifications + the unread
        count. Read-only, like /api/audit — gated only when `web.require_auth` is on. Empty when
        no history store is configured. A 30-day window is generous: notifications are sparse by
        construction (dedupe_key), so this never approaches the 500-row internal cap."""
        if ctx.store is None:
            return {"items": [], "unread": 0}
        now = datetime.now(UTC)
        start = now - timedelta(days=30)
        rows = await ctx.store.notifications_between(start.isoformat(), now.isoformat(), limit=500)
        items = list(reversed(rows))[:limit]  # newest-first for the dropdown feed
        return {"items": items, "unread": await ctx.store.unread_count()}

    @router.post("/api/notifications/read")
    async def mark_notifications_read_endpoint(body: dict | None = None) -> JSONResponse:
        """Mark notifications read: {"all": true} marks every unread row, {"ids": [1, 2, 3]} marks
        just those (an unknown id is silently ignored). Auth is enforced centrally by the
        _enforce_access middleware (writes always gated)."""
        if ctx.store is None:
            return JSONResponse({"detail": "history store not configured"}, status_code=503)
        body = body or {}
        mark_all = bool(body.get("all"))
        raw_ids = body.get("ids") if not mark_all else None
        ids: list[int] | None = None
        if isinstance(raw_ids, list):
            try:
                ids = [int(i) for i in raw_ids]
            except (TypeError, ValueError):
                return JSONResponse({"detail": "ids must be a list of integers"}, status_code=422)
        await ctx.store.mark_notifications_read(ids=ids, mark_all=mark_all)
        return JSONResponse({"unread": await ctx.store.unread_count()})

    return router
