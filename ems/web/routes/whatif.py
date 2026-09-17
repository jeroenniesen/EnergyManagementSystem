"""Counterfactual savings (BACKLOG B-69) + scenario simulator (BACKLOG B-73). Both ride the SAME
read-only replay engine (`ems.replay`, B-77) that already simulates no_battery / auto_selfuse /
planner over recorded history — this module never re-implements the battery math, it only
assembles + validates around `replay_range`.

GET /api/counterfactual — replay the last `days` recorded days under the CURRENT settings and
report the three scenarios the engine already computes, plus the plain-language deltas the
homeowner actually asks: "did this whole thing beat doing nothing, and does it beat the vendor's
own AUTO mode?" A fourth "naive cheapest-N-hours-a-day" baseline was in the original backlog card,
but is honestly SKIPPED: `ReplayConfig` has no knob that expresses "charge N fixed clock-hours a
day regardless of price shape" — `strategy.mode` is auto/summer/winter and the closest lever,
`planner.charge_slots`, only changes how many slots the WINTER planner's OWN price-rank picks, not
a naive fixed-hour rule. Faking it by hacking the engine would defeat the point of a shared,
trusted replay core, so this returns the three real scenarios instead of a fabricated fourth.
In-process cached for 15 minutes, keyed by days, local date and economic configuration.
Replaying a multi-day window on
every dashboard poll would be wasteful, and the local-today key means a new day naturally
invalidates it without any extra bookkeeping.

POST /api/whatif — replay the SAME kind of window twice: once under the CURRENT settings, once
under an ALLOW-LISTED override dict (an A/B `replay_range` call, exactly `test_replay.py`'s
cfg_a/cfg_b pattern). READ-ONLY BY CONSTRUCTION: `replay_range` opens the history DB `mode=ro` and
this handler never touches `settings_store` — nothing here is ever persisted, which is also why
POST /api/whatif is deliberately kept OUT of api.py's `_WRITE_API_PATHS`: gating it as a write
would tell a wrong story (it changes nothing, so there is nothing to protect). The allow-list is
narrow on purpose — only planner/battery/strategy/price knobs a homeowner would recognise as a
"what if"; no connection field, secret, or location could ever reach it even if `SETTINGS_BY_KEY`
grows a new field with the same prefix.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ems.replay import DayResult, RangeResult, ReplayConfig, replay_range
from ems.settings import validate_settings
from ems.web.context import AppContext

# The homeowner-facing counterfactual shows only the three ACHIEVABLE real-world baselines. The
# replay tool's `SCENARIOS` also carries `oracle` (a perfect-foresight hypothetical for the offline
# value-gap study) — deliberately NOT surfaced here, so this API contract stays stable.
_SCENARIOS = ("no_battery", "auto_selfuse", "planner")

_CACHE_TTL_SECONDS = 15 * 60.0

# The B-73 allow-list: only settings knobs a "what if" panel should ever be able to move.
# Deliberately narrower than the full SETTINGS_BY_KEY schema (see the module docstring) — checked
# BEFORE `validate_settings` so an unknown key is always reported as "not allowed", never
# coerced/dropped.
WHATIF_ALLOWED_KEYS: tuple[str, ...] = (
    "planner.solar_confidence",
    "planner.bill_optimization_enabled",
    "battery.min_reserve_soc",
    "planner.negative_price_soak",
    "prices.export_price_model",
    "strategy.summer_max_topup_price",
)


def _scenario_totals(days: list[DayResult]) -> dict[str, dict[str, float | None]]:
    """Sum each scenario's cost/import/export across every REPLAYABLE (`data_ok`) day — the same
    per-day `ScenarioResult`s `ems.replay._aggregate` sums for its own cost totals, just also
    carrying import/export (which the aggregate dict doesn't)."""
    ok = [d for d in days if d.data_ok]
    out: dict[str, dict[str, float | None]] = {}
    for name in _SCENARIOS:
        present = [d.scenarios[name] for d in ok if name in d.scenarios]
        costs = [s.cost_eur for s in present if s.cost_eur is not None]
        out[name] = {
            "cost_eur": round(sum(costs), 4) if costs else None,
            "import_kwh": round(sum(s.import_kwh for s in present), 3),
            "export_kwh": round(sum(s.export_kwh for s in present), 3),
            "grid_cost_eur": round(sum(costs), 4) if costs else None,
            "estimated_wear_eur": round(sum(s.estimated_wear_eur for s in present), 4)
            if costs
            else None,
            "inventory_adjustment_eur": round(sum(s.inventory_adjustment_eur for s in present), 4)
            if costs
            else None,
            "net_cost_eur": round(
                sum(s.net_cost_eur for s in present if s.net_cost_eur is not None), 4
            )
            if costs
            else None,
        }
    return out


def _counterfactual_note(days_used: int, delta_no_battery: float | None) -> str:
    if days_used == 0 or delta_no_battery is None:
        return "Not enough recorded history yet to compare — check back after a few days."
    verb = "beat" if delta_no_battery >= 0 else "trailed"
    plural = "s" if days_used != 1 else ""
    return (
        f"The simulated grid bill {verb} the no-battery baseline "
        f"by €{abs(delta_no_battery):.2f} over {days_used} "
        f"recorded day{plural}; wear and stored-energy changes are shown separately."
    )


def build_counterfactual(result: RangeResult, days_requested: int) -> dict:
    """Pure assembly of the /api/counterfactual response from an already-computed `RangeResult`
    (BACKLOG B-69) — split out from the route handler so the shape/math is unit-testable without a
    DB, an app, or the in-process cache."""
    agg = result.aggregate
    days_used = int(agg["days_replayed"])
    days_skipped = int(agg["days_skipped"])
    scenarios = _scenario_totals(result.days)
    delta_no_battery = agg["planner_vs_no_battery_eur"] if days_used else None
    delta_auto = agg["planner_vs_auto_eur"] if days_used else None
    window: dict[str, Any] | None = None
    ok_dates = [d.date for d in result.days if d.data_ok]
    if ok_dates:
        window = {"start": min(ok_dates), "end": max(ok_dates), "days_requested": days_requested}
    return {
        "window": window,
        "simulation": True,
        "limitations": agg.get("limitations", []),
        "continuity_resets": agg.get("continuity_resets", 0),
        "days_used": days_used,
        "days_skipped": days_skipped,
        "scenarios": scenarios,
        "deltas": {
            "planner_vs_no_battery": delta_no_battery,
            "planner_vs_auto": delta_auto,
            "planner_vs_auto_net_eur": agg.get("planner_vs_auto_net_eur") if days_used else None,
            "planner_vs_no_battery_net_eur": agg.get("planner_vs_no_battery_net_eur")
            if days_used
            else None,
        },
        "note": _counterfactual_note(days_used, delta_no_battery),
    }


def _whatif_note(days_used: int, delta_eur: float | None) -> str:
    if days_used == 0 or delta_eur is None:
        return "Not enough recorded history yet to simulate this."
    plural = "s" if days_used != 1 else ""
    if delta_eur > 0.005:
        return (
            f"The simulated grid bill would have been ≈ €{delta_eur:.2f} lower "
            f"over the last {days_used} "
            f"recorded day{plural}; wear and stored-energy changes are shown separately."
        )
    if delta_eur < -0.005:
        return (
            f"The simulated grid bill would have been ≈ €{abs(delta_eur):.2f} higher "
            f"over the last {days_used} "
            f"recorded day{plural}; wear and stored-energy changes are shown separately."
        )
    return (
        f"The simulated grid bill shows almost no difference over the last {days_used} "
        f"recorded day{plural}; wear and stored-energy changes are shown separately."
    )


def build_whatif(result: RangeResult, overrides: dict[str, Any], days_requested: int) -> dict:
    """Pure assembly of the /api/whatif response from an A/B `RangeResult` (BACKLOG B-73) — mirrors
    `build_counterfactual`'s split so the math is unit-testable in isolation."""
    agg = result.aggregate
    days_used = int(agg["days_replayed"])
    days_skipped = int(agg["days_skipped"])
    cfg_b_agg = agg.get("cfg_b") or {}
    baseline_cost = agg["planner_cost_eur"] if days_used else None
    variant_cost = cfg_b_agg.get("planner_cost_eur") if days_used else None
    # `delta_vs_a_eur` = A_cost - B_cost, so + means the OVERRIDE (B / "variant") is cheaper —
    # exactly the "would have saved €X" sign a homeowner expects.
    delta_eur = cfg_b_agg.get("delta_vs_a_eur") if days_used else None

    per_day: list[dict[str, Any]] = []
    if result.days_b is not None:
        for day_a, day_b in zip(result.days, result.days_b, strict=False):
            if not (day_a.data_ok and day_b.data_ok):
                continue
            cost_a = day_a.scenarios["planner"].cost_eur
            cost_b = day_b.scenarios["planner"].cost_eur
            day_delta = None if cost_a is None or cost_b is None else round(cost_a - cost_b, 4)
            per_day.append(
                {
                    "date": day_a.date,
                    "baseline_eur": cost_a,
                    "variant_eur": cost_b,
                    "delta_eur": day_delta,
                }
            )

    return {
        "simulation": True,
        "days": days_requested,
        "days_used": days_used,
        "days_skipped": days_skipped,
        "overrides": overrides,
        "baseline": {
            "cost_eur": baseline_cost,
            "net_cost_eur": agg.get("planner_net_cost_eur") if days_used else None,
            "estimated_wear_eur": agg.get("planner_estimated_wear_eur") if days_used else None,
            "inventory_adjustment_eur": agg.get("planner_inventory_adjustment_eur")
            if days_used
            else None,
        },
        "variant": {
            "cost_eur": variant_cost,
            "net_cost_eur": cfg_b_agg.get("planner_net_cost_eur") if days_used else None,
            "estimated_wear_eur": cfg_b_agg.get("planner_estimated_wear_eur")
            if days_used
            else None,
            "inventory_adjustment_eur": cfg_b_agg.get("planner_inventory_adjustment_eur")
            if days_used
            else None,
        },
        "net_delta_eur": cfg_b_agg.get("net_delta_vs_a_eur") if days_used else None,
        "limitations": agg.get("limitations", []),
        "delta_eur": delta_eur,
        "per_day": per_day,
        "note": _whatif_note(days_used, delta_eur),
    }


def _empty_counterfactual(note: str) -> dict:
    return {
        "window": None,
        "simulation": True,
        "limitations": [],
        "days_used": 0,
        "days_skipped": 0,
        "scenarios": {
            name: {"cost_eur": None, "import_kwh": 0.0, "export_kwh": 0.0} for name in _SCENARIOS
        },
        "deltas": {"planner_vs_no_battery": None, "planner_vs_auto": None},
        "note": note,
    }


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    # In-process only (BACKLOG B-69) — NOT `ctx.cache_store` (that one's SQLite-persisted, meant to
    # survive a restart; this is a plain per-worker memoisation of an expensive read). A fresh
    # closure per `build_router` call keeps it scoped to one app instance (and one test).
    _cache: dict[tuple[int, str, ReplayConfig], tuple[float, dict]] = {}

    def _replay_ab(days: int, overrides: dict[str, Any]) -> RangeResult:
        base = dict(ctx.settings_cache)
        cfg_a = ReplayConfig.from_settings(base, tz=ctx.site_tz)
        cfg_b = ReplayConfig.from_settings({**base, **overrides}, tz=ctx.site_tz)
        return replay_range(ctx.store, days, cfg_a, cfg_b=cfg_b)

    @router.get("/api/counterfactual")
    async def counterfactual_endpoint(days: int = Query(default=14, ge=1, le=90)) -> dict:
        """B-69: no_battery / auto_selfuse / planner totals over the last `days` recorded days,
        under the CURRENT settings. Read-only; see the module docstring for the cache and for why
        there is no fourth "naive cheapest hours" scenario."""
        if ctx.store is None:
            return _empty_counterfactual("No history store configured yet.")
        today = datetime.now(ctx.site_tz).date().isoformat()
        cfg = ReplayConfig.from_settings(dict(ctx.settings_cache), tz=ctx.site_tz)
        key = (days, today, cfg)
        now_mono = time.monotonic()
        cached = _cache.get(key)
        if cached is not None and (now_mono - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]
        result = await asyncio.to_thread(replay_range, ctx.store, days, cfg)
        payload = build_counterfactual(result, days)
        _cache[key] = (now_mono, payload)
        return payload

    @router.post("/api/whatif")
    async def whatif_endpoint(body: dict | None = None) -> dict:
        """B-73: replay the current settings vs. an allow-listed override dict over the same
        window (`{overrides, days}`), a pure A/B `replay_range` call. Auth: deliberately OUTSIDE
        `_WRITE_API_PATHS` in api.py — a simulation is read-only by construction (see the module
        docstring), so gating it like a write would misrepresent what it does."""
        if ctx.store is None:
            return JSONResponse(  # type: ignore[return-value]
                {"detail": "history store not configured"}, status_code=503
            )
        body = body if isinstance(body, dict) else {}
        overrides = body.get("overrides")
        if not isinstance(overrides, dict):
            return JSONResponse(  # type: ignore[return-value]
                {"detail": "overrides must be an object"}, status_code=422
            )

        unknown = sorted(k for k in overrides if k not in WHATIF_ALLOWED_KEYS)
        if unknown:
            return JSONResponse(  # type: ignore[return-value]
                {
                    "detail": "unknown override key(s)",
                    "errors": {k: "not an allowed simulation setting" for k in unknown},
                },
                status_code=422,
            )
        clean, errors = validate_settings(overrides)
        if errors:
            return JSONResponse(  # type: ignore[return-value]
                {"detail": "invalid override value(s)", "errors": errors}, status_code=422
            )

        days_raw = body.get("days", 7)
        if isinstance(days_raw, bool) or not isinstance(days_raw, (int, float)):
            return JSONResponse(  # type: ignore[return-value]
                {"detail": "days must be a number"}, status_code=422
            )
        days = max(1, min(90, int(days_raw)))

        result = await asyncio.to_thread(_replay_ab, days, clean)
        return build_whatif(result, clean, days)

    return router
