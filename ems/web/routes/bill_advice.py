"""Read-only bill, calibration and appliance advice on existing household evidence."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from ems.bill_advice import appliance_window, consumption_advice, reserve_advice
from ems.calibration import battery_calibration, load_accuracy
from ems.planner.load_profile import build_load_profile
from ems.sources.prices import PriceSlot
from ems.tariff_history import economic_snapshot_at
from ems.web.context import AppContext, history_row_cap

_log = logging.getLogger(__name__)


class ApplianceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deadline: datetime
    duration_minutes: int = Field(ge=15, le=1440, multiple_of=15)
    energy_kwh: FiniteFloat = Field(gt=0, le=50)


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    async def evidence(now):
        raw, derived = [], []
        if ctx.store:
            start = now - timedelta(days=42)
            limit = history_row_cap((now - start).total_seconds(), ctx.sample_cadence_seconds())
            raw, derived = await asyncio.gather(
                ctx.store.raw_between(start.isoformat(), now.isoformat(), limit=limit),
                ctx.store.derived_between(start.isoformat(), now.isoformat(), limit=limit),
            )
        return raw, derived

    def inputs(now):
        try:
            prices = ctx.price_source.slots() if ctx.price_source else []
            forecast = ctx.solar_forecast.slots() if ctx.solar_forecast else []
        except Exception:
            _log.warning("Advice sources unavailable", exc_info=True)
            return [], [], {}, False
        settings = dict(ctx.settings_cache)
        normalized, exports = [], {}
        for p in prices:
            snap = economic_snapshot_at(settings, p.start, p.eur_per_kwh, str(ctx.site_tz))
            if snap is None:
                continue
            normalized.append(PriceSlot(p.start, snap.import_price_eur_per_kwh))
            exports[p.start] = snap.export_credit()
        fresh = ctx.data_quality(now) == "complete"
        return normalized, forecast if fresh else [], exports, fresh

    @router.get("/api/bill-advice")
    async def advice() -> dict:
        now = datetime.now(UTC)
        raw, derived = await evidence(now)
        solar = await ctx.solar_forecast_skill(now) if ctx.store else None

        def compute():
            prices, forecast, _, fresh = inputs(now)
            profile = build_load_profile(derived, ctx.site_tz, enhanced=True, as_of=now)
            p = [x.eur_per_kwh for x in prices if now <= x.start < now + timedelta(hours=24)]
            consumption = consumption_advice(
                derived, now=now, tz=ctx.site_tz, price_eur_per_kwh=sum(p) / len(p) if p else 0
            )
            if not p:
                for item in consumption:
                    item["estimated_extra_eur_per_day"] = None
            return {
                "automatic": False,
                "reserve": reserve_advice(
                    prices,
                    forecast,
                    profile,
                    now=now,
                    settings=ctx.settings_cache,
                    fresh=fresh and bool(derived) and bool(forecast),
                ),
                "calibration": battery_calibration(
                    raw, configured_kwh=ctx.settings_cache["battery.usable_kwh"]
                ),
                "load_accuracy": load_accuracy(derived, now=now, tz=ctx.site_tz),
                "consumption": consumption,
                "solar": solar,
                "basis": "Advice only; settings and devices are unchanged. "
                "Consumption costs use upcoming average import prices.",
            }

        return await asyncio.to_thread(compute)

    @router.post("/api/advisor/appliance")
    async def appliance(body: ApplianceRequest) -> dict:
        now = datetime.now(UTC)
        _, derived = await evidence(now)

        def compute():
            prices, forecast, exports, _ = inputs(now)
            profile = build_load_profile(derived, ctx.site_tz, enhanced=True, as_of=now)
            return appliance_window(
                prices,
                forecast if derived else [],
                {p.start: profile.expected_w(p.start) for p in prices},
                now=now,
                deadline=body.deadline,
                duration_minutes=body.duration_minutes,
                energy_kwh=body.energy_kwh,
                export_by=exports,
            )

        try:
            return await asyncio.to_thread(compute)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
