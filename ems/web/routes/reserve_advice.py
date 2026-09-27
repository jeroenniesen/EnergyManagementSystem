"""Read-only night-reserve recommendation (B-67 / #74).

GET /api/advisor/reserve — advice only. Never writes the settings store and never calls
ModeController.decide / battery drivers.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from ems.planner.load_profile import LoadProfile, build_load_profile
from ems.reserve_advice import night_demand_kwh, recommend_night_reserve
from ems.sky import sun_times
from ems.web.context import AppContext, history_row_cap

_log = logging.getLogger(__name__)


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/advisor/reserve")
    async def advisor_reserve() -> dict:
        """Tonight's night-reserve advice next to the user's configured buffer. Advice only."""
        now = datetime.now(UTC)
        settings = ctx.settings_cache
        # Snapshot settings values used for the advice — never mutate the live cache.
        usable = float(settings["battery.usable_kwh"])
        min_reserve = float(settings["battery.min_reserve_soc"])
        night_reserve = float(settings["battery.night_reserve_kwh"])
        overnight_cfg = float(settings["battery.overnight_load_kwh"])
        eta = float(settings["planner.round_trip_efficiency"])
        lat = float(settings.get("site.lat", 52.13))
        lon = float(settings.get("site.lon", 5.29))

        demand: float | None = None
        try:
            local = now.astimezone(ctx.site_tz)
            today = local.date()
            sunrise_today, sunset_today = sun_times(lat, lon, today, ctx.site_tz)
            sunrise_tomorrow, _ = sun_times(lat, lon, today + timedelta(days=1), ctx.site_tz)
            _, sunset_yesterday = sun_times(lat, lon, today - timedelta(days=1), ctx.site_tz)
            if sunrise_today is not None and local < sunrise_today:
                sunset, sunrise = sunset_yesterday, sunrise_today
            else:
                sunset, sunrise = sunset_today, sunrise_tomorrow
            if sunset is None or sunrise is None:
                raise ValueError("polar day/night — no overnight window")

            forecast_ok = ctx.data_quality(now) == "complete" and ctx.solar_forecast is not None
            if forecast_ok:
                try:
                    forecast_ok = bool(ctx.solar_forecast.slots())
                except Exception:
                    _log.debug("solar forecast unavailable for reserve advice", exc_info=True)
                    forecast_ok = False

            profile: LoadProfile | None = None
            if forecast_ok and ctx.store is not None:
                start = now - timedelta(days=21)
                limit = history_row_cap(
                    (now - start).total_seconds(), ctx.sample_cadence_seconds()
                )
                rows = await ctx.store.derived_between(
                    start.isoformat(), now.isoformat(), limit=limit
                )
                profile = await asyncio.to_thread(build_load_profile, rows, ctx.site_tz)

            if forecast_ok:
                load_profile = profile or LoadProfile({}, ctx.site_tz)
                demand = await asyncio.to_thread(
                    night_demand_kwh, load_profile.expected_w, sunset, sunrise
                )
        except Exception:
            _log.debug("reserve advice night window failed — falling back", exc_info=True)
            demand = None

        advice = recommend_night_reserve(
            night_demand_kwh=demand,
            usable_kwh=usable,
            min_reserve_soc=min_reserve,
            night_reserve_kwh=night_reserve,
            overnight_load_kwh=overnight_cfg,
            round_trip_efficiency=eta,
        )
        return {"advice": advice, "automatic": False}

    return router
