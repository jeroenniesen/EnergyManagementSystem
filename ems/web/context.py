"""Minimal app-context for the web routers (BACKLOG B-25, incremental slice).

A boring, explicit dataclass carrying the shared singletons + the small helper CALLABLES the
extracted route domains (car / digest / notify / export / accuracy) need, so each of those can be a
`build_router(ctx: AppContext) -> APIRouter` in its own module instead of a closure buried in
`create_app`. The FULL app-context / control-service extraction is B-46 and deliberately OUT OF
SCOPE here — this is only the incremental slice B-25's own text calls for.

IMPORTANT: `settings_cache` is THE live effective-settings dict — mutated in place by the lifespan
and by POST /api/settings (never rebound), so every router observing it through this context sees a
just-saved value without a restart. Always pass the reference, never a copy.

The helper callables (`data_quality`, `ensure_day_finance`, `report_for_window`, ...) stay defined
as closures in `create_app` for now; routers reach them through this context rather than owning
their gathering. Keeping them here as `Callable` fields is the seam B-46 later replaces with real
service objects.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ems.notify import Notifier
from ems.sense import Recorder
from ems.sources.base import Source
from ems.sources.forecast import SolarForecastSource
from ems.sources.prices import PriceSource
from ems.storage.audit import AuditStore
from ems.storage.auth import AuthStore
from ems.storage.cache import CacheStore
from ems.storage.history import HistoryStore


def history_row_cap(
    span_seconds: float,
    cycle_seconds: float,
    *,
    margin: float = 2.0,
    floor: int = 1000,
    ceiling: int = 200_000,
) -> int:
    """Row limit for a history query spanning `span_seconds`, sized to the recorder cadence rather
    than hardcoded (finding 10). The recorder writes ~one row every `cycle_seconds`, so a report
    stays correct if the sampling frequency changes (e.g. faster in dev) instead of silently
    truncating at a fixed 3000/day or a 1-row-per-minute ceiling. `margin` gives headroom for
    write jitter; the result is clamped to `[floor, ceiling]`."""
    cadence = max(float(cycle_seconds), 1.0)
    rows = int(max(span_seconds, 0.0) / cadence) + 1
    return max(floor, min(ceiling, int(rows * margin)))


@dataclass
class AppContext:
    """Shared singletons + helper callables the extracted routers need. Built once in
    `create_app` and handed to each `build_router(ctx)`."""

    # --- shared singletons (same object instances create_app holds) ----------------------------
    source: Source
    store: HistoryStore | None
    # THE live effective-settings dict — mutated in place, never rebound (pass the reference).
    settings_cache: dict[str, Any]
    audit_store: AuditStore | None
    auth_store: AuthStore | None
    cache_store: CacheStore | None
    notifier: Notifier | None
    price_source: PriceSource | None
    solar_forecast: SolarForecastSource | None
    recorder: Recorder | None
    site_tz: ZoneInfo
    # The raw tz arg (may be None); the export manifest records str(tz) verbatim.
    tz: ZoneInfo | None
    dry_run: bool
    dev_mode: str
    # Planning knobs allow-list included in the export manifest (privacy §12) — passed by value so
    # the export router never has to import it back from api.py (which would be a circular import).
    replay_setting_keys: tuple[str, ...]

    # --- shared helper callables (defined in create_app; routers call them through the ctx) -----
    car_charging: Callable[[datetime], bool]
    data_quality: Callable[[datetime], str]
    sample_cadence_seconds: Callable[[], float]
    capability_present: Callable[[], bool]
    ensure_day_finance: Callable[[date_cls], Awaitable[dict]]
    solar_forecast_skill: Callable[[datetime], Awaitable[dict | None]]
    solar_confidence_advice: Callable[[datetime], Awaitable[dict | None]]
    report_for_window: Callable[..., Awaitable[dict]]
    effective_web_token: Callable[[], str | None]
