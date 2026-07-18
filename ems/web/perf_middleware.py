"""Pure-ASGI request timing middleware for B-80.

Wraps every /api/ request, records (method, path_template, duration_ms, status)
into the perf Registry, and tags the sample as over_budget against the per-tier
budget. Does NOT cancel slow requests — over-budget is a measurement, not
rate-limiting.

Pure ASGI by construction (subclass of object with __call__(scope, receive, send))
so the override control cycle stays unstarved. See auth-slice invariant for the
reasoning.
"""

from __future__ import annotations

import logging
import re
import time

from ems.perf import PERF_BUDGETS, REGISTRY

_log = logging.getLogger("ems.perf.middleware")

# Path prefixes classified as H (hot/dashboard-10s).
HOT_API_PREFIXES: tuple[str, ...] = (
    "/api/status", "/api/freshness", "/api/energy-story", "/api/battery-plan",
    "/api/strategy", "/api/battery", "/api/decision", "/api/alerts",
    "/api/finance", "/api/charge-need", "/api/car/plan", "/api/override",
)

# Path prefixes classified as B (batch).
BATCH_API_PREFIXES: tuple[str, ...] = (
    "/api/export/package", "/api/counterfactual", "/api/digest",
    "/api/car/sessions", "/api/advisor/ev-charge",
)


def classify_path(path_template: str) -> str:
    """Return 'hot', 'batch', or 'interactive'."""
    for prefix in HOT_API_PREFIXES:
        if path_template.startswith(prefix):
            return "hot"
    for prefix in BATCH_API_PREFIXES:
        if path_template.startswith(prefix):
            return "batch"
    return "interactive"


_QUERY_RE = re.compile(r"\?.*$")


def _strip_query(path: str) -> str:
    return _QUERY_RE.sub("", path)


class PerfTimingMiddleware:
    """Pure-ASGI timing wrapper.

    Usage:
        app.add_middleware(PerfTimingMiddleware)

    Records every /api/ request's wall-clock duration into the perf Registry.
    Over-budget requests log WARN but the response is delivered normally.
    """

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            # Lifespan / websocket: pass through unchanged.
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/api/"):
            # Static assets, SPA fallback, health — not in scope for perf tracking.
            await self.app(scope, receive, send)
            return

        path_template = _strip_query(path)
        tier = classify_path(path_template)
        sample_name = f"api.{tier}"
        budget_ms = PERF_BUDGETS.get(sample_name)

        t0 = time.perf_counter()
        status_holder = {"code": 500}  # default if the handler crashes

        async def send_wrapper(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000
            sample = REGISTRY.push(
                sample_name,
                duration_ms,
                extra={
                    "status": status_holder["code"],
                    "path_template": path_template,
                },
            )
            if sample.over_budget:
                _log.warning(
                    "perf.over_budget name=%s duration_ms=%.1f budget_ms=%s "
                    "status=%s path=%s",
                    sample_name, duration_ms, budget_ms,
                    status_holder["code"], path_template,
                )
