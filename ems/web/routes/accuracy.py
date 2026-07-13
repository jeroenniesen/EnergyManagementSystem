"""Forecast/prediction-accuracy routes (BACKLOG B-72 slice, extracted from create_app).

GET /api/accuracy (all three tracks) · GET /api/advisor/solar-confidence (advisory hint).

Both are read-only and gathered off the shared ctx helpers: `solar_forecast_skill` and
`solar_confidence_advice` stay defined in api.py (they are reused by the control/notify path there)
and are reached through the context; only the two extra tracks (`plan_execution_error`,
`load_baseline_error`) and their store reads live here.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from ems.analysis import load_baseline_error, plan_execution_error
from ems.web.context import AppContext, history_row_cap


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/advisor/solar-confidence")
    async def advisor_solar_confidence() -> dict:
        """Advisory-only recommendation for `planner.solar_confidence` — the Settings UI renders
        this as a hint next to the field; the user decides. Read-only, gated like any other
        /api/* read (only if `web.require_auth` is on) — see /api/report."""
        return {"advice": await ctx.solar_confidence_advice(datetime.now(UTC))}

    @router.get("/api/accuracy")
    async def accuracy() -> dict:
        """All three forecast/prediction-accuracy tracks (B-72) in one read-only call — solar
        forecast skill, plan-execution (target_soc-by-deadline vs. achieved SoC), and load-baseline
        (household load vs. a naive day-of-week/hour trailing mean, the bar B-64 must beat). Each
        is `None` below its own measurable-evidence minimum (see `ems.analysis`), independently.
        Gathered the same way as /api/advisor/solar-confidence: solar over the last 14 days (the
        same evidence window as that advisor); plan-execution and load need more history to reach
        their evidence minimums (deadlines are ~daily, day-of-week/hour baselines need several
        weeks), so those two are gathered over the last 60 days instead."""
        solar = None
        plan_execution = None
        load = None
        if ctx.store is not None:
            now = datetime.now(UTC)

            solar = await ctx.solar_forecast_skill(now)

            long_start = now - timedelta(days=60)
            plan_rows = await ctx.store.plan_history_between(
                long_start.isoformat(), now.isoformat())
            plan_execution = plan_execution_error(plan_rows, tz=ctx.site_tz)

            long_limit = history_row_cap(
                (now - long_start).total_seconds(), ctx.sample_cadence_seconds())
            long_raw = await ctx.store.raw_between(
                long_start.isoformat(), now.isoformat(), limit=long_limit)
            load = load_baseline_error(long_raw, tz=ctx.site_tz)
        return {"solar": solar, "plan_execution": plan_execution, "load": load}

    return router
