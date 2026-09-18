"""Tariff settings and read-only observed invoice comparison; auth is the app-wide gate."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, FiniteFloat

from ems.finance import reconcile_invoice
from ems.storage.settings import SettingsStore
from ems.tariff_history import append_period, finance_tariff_kwargs
from ems.tariffs import TariffPeriod
from ems.web.context import AppContext, history_row_cap


class InvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    invoice_eur: FiniteFloat
    fixed_cost_eur: FiniteFloat


def build_router(ctx: AppContext, settings_store: SettingsStore | None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/tariffs")
    async def get_tariffs() -> dict:
        values = await settings_store.all() if settings_store is not None else ctx.settings_cache
        return {
            "periods": values.get("tariffs.periods", []),
            "legacy": values.get("tariffs.legacy"),
            "basis": "VAT-inclusive EUR/kWh; end_date exclusive in site timezone; "
            "immutable periods",
            "timezone": str(ctx.site_tz),
        }

    @router.post("/api/tariffs")
    async def add_tariff(body: dict) -> dict:
        if settings_store is None:
            raise HTTPException(503, "Settings persistence is unavailable")
        try:
            period = TariffPeriod(**body)
            await append_period(settings_store, ctx.settings_cache, period)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if ctx.audit_store is not None:
            await ctx.audit_store.append(
                datetime.now(UTC).isoformat(),
                "config_change",
                f"Added tariff period {period.start_date} to {period.end_date} (exclusive)",
                {"start_date": period.start_date, "end_date": period.end_date},
            )
        return await get_tariffs()

    @router.post("/api/invoice-reconciliation")
    async def invoice(body: InvoiceRequest) -> dict:
        days = (body.end_date - body.start_date).days
        if not 1 <= days <= 366:
            raise HTTPException(
                422, "Invoice window must span 1 to 366 days; end_date is exclusive"
            )
        start = datetime.combine(body.start_date, time.min, ctx.site_tz).astimezone(UTC)
        end = datetime.combine(body.end_date, time.min, ctx.site_tz).astimezone(UTC)
        raw, prices = [], []
        cadence = ctx.sample_cadence_seconds() if ctx.store is not None else 300.0
        if ctx.store is not None:
            raw, prices = await asyncio.gather(
                ctx.store.raw_between(
                    start.isoformat(),
                    end.isoformat(),
                    limit=history_row_cap((end - start).total_seconds(), cadence),
                ),
                ctx.store.prices_between(start.isoformat(), end.isoformat()),
            )
        settings = dict(ctx.settings_cache)
        if settings_store is not None:
            settings.update(await settings_store.all())
        return reconcile_invoice(
            raw,
            prices,
            start=start,
            end=end,
            invoice_eur=body.invoice_eur,
            fixed_cost_eur=body.fixed_cost_eur,
            sample_interval_seconds=cadence,
            max_hold_seconds=2 * cadence,
            tariff_timezone=str(ctx.site_tz),
            **finance_tariff_kwargs(settings),
        )

    return router
