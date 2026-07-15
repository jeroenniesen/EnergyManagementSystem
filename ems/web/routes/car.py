"""Car / EV advisory routes (BACKLOG B-25 slice, extracted from create_app).

GET /api/cars · GET /api/car/plan · POST /api/car/soc, plus the `gather_car_plan` gathering helper
that `_run_detector_cycle` (B-75 `ev_plug_in_reminder`) in api.py also calls — so it is a
module-level function taking the ctx, not a closure, exactly like `_run_weekly_digest`.

AUTH: POST /api/car/soc is a write; it is gated centrally by `_AccessMiddleware` in api.py, whose
`_WRITE_API_PATHS` set includes "/api/car/soc". Moving the handler here does NOT change its path, so
the middleware still guards it — keep the path in that set if this route is ever renamed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from ems.cars import CARS
from ems.cars import brands as car_brands
from ems.cars import by_id as car_by_id
from ems.cars import to_dict as car_to_dict
from ems.ev_planner import plan_car_charging
from ems.ev_schedule import materialize_deadlines, parse_schedule
from ems.ev_session import detect_sessions, estimate_soc
from ems.web.context import AppContext, history_row_cap


async def car_soc_estimate(
    ctx: AppContext, now: datetime, anchor_pct: float, anchor_ts: str
) -> dict | None:
    """Estimate the car SoC from a manual anchor + charging measured over the last 14 days of
    recorded samples (ems/ev_session.estimate_soc). Shared by GET /api/car/plan and the block
    POST /api/car/soc echoes back. `store` must not be None (both call sites guard it). Returns
    None only for a degenerate anchor/capacity (estimate_soc's own contract)."""
    start = now - timedelta(days=14)
    limit = history_row_cap((now - start).total_seconds(), ctx.sample_cadence_seconds())
    rows = await ctx.store.raw_between(start.isoformat(), now.isoformat(), limit=limit)
    return estimate_soc(
        rows,
        anchor_pct=anchor_pct,
        anchor_ts=anchor_ts,
        battery_net_kwh=float(ctx.settings_cache["ev.battery_kwh"]),
        now=now,
        charge_efficiency=float(ctx.settings_cache["ev.charge_efficiency"]),
        # Use the SAME threshold the live car-guard uses to detect charging, so a slow (1-phase /
        # reduced-amp) charge that trips the guard is also COUNTED here — otherwise the estimate
        # reads low and the charge plan over-buys (detection/accounting mismatch).
        threshold_w=float(ctx.settings_cache["control.car_charging_threshold_w"]),
    )


async def gather_car_plan(ctx: AppContext, now: datetime) -> dict:
    """The EV feature's main read (design 2026-07-12): when to plug in the car to meet the
    weekly minimum-charge schedule as cheaply as possible. Advisory only — never commands
    anything. Wires ems/ev_schedule + ems/ev_session + ems/ev_planner to settings, the manual
    SoC anchor, and the SAME price/forecast access as /api/advisor/ev-charge.

    Progressive states so the UI can prompt for what's missing: `enabled:false` (feature off),
    `needs_anchor` (no SoC set — "set your car's charge level"), `needs_schedule` (nothing
    enabled in the weekly schedule), else the full plan. `soc.stale` (>72 h) is carried in the
    soc block and does NOT stop planning — the plan is still shown with the staleness flag.

    Extracted from the GET /api/car/plan handler (`now` is the only thing that varies) so
    `_run_detector_cycle` (B-75 `ev_plug_in_reminder`) reuses the EXACT same gathering instead
    of duplicating it."""
    s = ctx.settings_cache
    if not s.get("ev.advice_enabled"):
        return {"enabled": False, "plan": None, "soc": None}

    car_meter_configured = bool(str(s.get("meters.car_ip") or "").strip())

    # --- car SoC estimate from the manual anchor (no anchor ⇒ prompt to set one) ---
    soc = None
    if ctx.store is not None:
        anchor = await ctx.store.get_car_soc_anchor()
        if anchor is not None:
            soc = await car_soc_estimate(ctx, now, anchor_pct=anchor[0], anchor_ts=anchor[1])
    if soc is None:
        return {
            "enabled": True, "plan": None, "soc": None, "needs_anchor": True,
            "car_meter_configured": car_meter_configured,
        }

    # --- weekly schedule → concrete, tz-aware deadlines (empty ⇒ prompt to set a schedule) ---
    schedule = parse_schedule(s.get("ev.schedule"))
    deadlines = materialize_deadlines(schedule, now, ctx.site_tz)
    if not deadlines:
        return {
            "enabled": True, "soc": soc, "plan": None, "needs_schedule": True,
            "car_meter_configured": car_meter_configured,
        }

    # --- effective charge power = min(charger, car AC limit) (charger alone if no car) ---
    car = car_by_id(str(s.get("ev.car_id") or ""))
    charger_kw = float(s["ev.charger_kw"])
    effective_kw = min(charger_kw, car.max_ac_kw) if car is not None else charger_kw

    # --- prices + solar P50, gathered exactly like /api/advisor/ev-charge ---
    prices = ctx.price_source.slots() if ctx.price_source is not None else []
    forecast = ctx.solar_forecast.slots() if ctx.solar_forecast is not None else []
    p50_map = {f.start: f.p50_w for f in forecast}

    # surplus_threshold_w is left at plan_car_charging's default (1000 W) — the same surplus
    # threshold the advisor uses, so both price a sunny slot the same way.
    plan = plan_car_charging(
        now,
        deadlines,
        prices,
        p50_map,
        soc_pct=soc["soc_pct"],
        battery_net_kwh=float(s["ev.battery_kwh"]),
        charge_efficiency=float(s["ev.charge_efficiency"]),
        power_kw=effective_kw,
        export_model=str(s.get("prices.export_price_model", "net_metering")),
        energy_tax_eur_per_kwh=float(s.get("prices.energy_tax_eur_per_kwh", 0.13)),
        fixed_feed_in_eur_per_kwh=float(s.get("prices.fixed_feed_in_eur_per_kwh", 0.01)),
    )
    return {
        "enabled": True,
        "soc": soc,
        "plan": plan,
        "schedule": schedule,
        "effective_kw": effective_kw,
        "car": car_to_dict(car) if car is not None else None,
        "car_meter_configured": car_meter_configured,
    }


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/cars")
    def cars_endpoint() -> dict:
        """Static car-picker data (ems/cars.py) for the Settings "Car" group: sorted brand list
        + every model as a plain dict. Read-only and cacheable — the dataset never changes at
        runtime, so this never touches settings/store."""
        return {"brands": car_brands(), "cars": [car_to_dict(c) for c in CARS]}

    @router.get("/api/car/plan")
    async def car_plan() -> dict:
        return await gather_car_plan(ctx, datetime.now(UTC))

    @router.get("/api/car/sessions")
    async def car_sessions(days: int = Query(default=14, ge=1, le=90)) -> dict:
        """Detected EV charging sessions over the last `days`, newest-first, for the Car tab's
        history table. Sessions are computed ON DEMAND from the already-recorded raw samples
        (ems/ev_session.detect_sessions — no recorder state machine), reusing the SAME gathering
        the export package does. Read-only; returns an empty list (never an error) when there is no
        history store or no charging in the window, so the UI can render an honest empty state.
        Shape: [{start, end, kwh, avg_kw, peak_kw}]."""
        if ctx.store is None:
            return {"sessions": [], "days": days}
        now = datetime.now(UTC)
        start = now - timedelta(days=days)
        limit = history_row_cap((now - start).total_seconds(), ctx.sample_cadence_seconds())
        rows = await ctx.store.raw_between(start.isoformat(), now.isoformat(), limit=limit)
        sessions = [
            {"start": s["start"], "end": s["end"], "kwh": s["kwh"],
             "avg_kw": s["avg_kw"], "peak_kw": s["peak_kw"]}
            for s in detect_sessions(rows)
        ]
        sessions.reverse()  # detect_sessions is chronological; the history table reads newest-first
        return {"sessions": sessions, "days": days}

    @router.post("/api/car/soc")
    async def set_car_soc(request: Request, body: dict | None = None) -> JSONResponse:
        """Set the manual car-SoC anchor (a percent, timestamped now) — the app's SoC "ground
        truth" it estimates forward from. Auth is enforced centrally by _AccessMiddleware (this
        path is in _WRITE_API_PATHS) and the write is audited exactly like POST /api/override."""
        if ctx.store is None:
            return JSONResponse({"detail": "history store not configured"}, status_code=503)
        body = body or {}
        pct = body.get("pct")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            return JSONResponse(
                {"detail": "invalid car soc", "errors": {"pct": "must be a number"}},
                status_code=422)
        if not (0 <= pct <= 100):
            return JSONResponse(
                {"detail": "invalid car soc", "errors": {"pct": "must be between 0 and 100"}},
                status_code=422)
        now = datetime.now(UTC)
        await ctx.store.set_car_soc_anchor(float(pct), now.isoformat())
        if ctx.audit_store is not None:
            await ctx.audit_store.append(
                now.isoformat(), "car_soc_anchor",
                f"Car SoC anchored at {pct:g}%",
                {"pct": float(pct), "ts": now.isoformat()},
            )
        soc = await car_soc_estimate(ctx, now, anchor_pct=float(pct), anchor_ts=now.isoformat())
        return JSONResponse({"soc": soc})

    return router
