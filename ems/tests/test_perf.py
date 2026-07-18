"""Tests for B-80 perf budgets. See docs/superpowers/specs/2026-07-18-perf-budgets-design.md."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ems.perf import REGISTRY
from ems.web.perf_middleware import PerfTimingMiddleware

SPEC_DOC = (Path(__file__).resolve().parents[2] / "docs" / "perf-budgets.md")


def _parse_spec_budgets() -> dict[str, float]:
    """Parse the budgets markdown table: rows are `| name | tier | budget | where |`."""
    text = SPEC_DOC.read_text()
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        name = cells[0]
        # Skip header / separator rows.
        if name in {"Name", "---"} or name.startswith("---"):
            continue
        budget_cell = cells[2]
        # Budget cells are like "500 ms", "20 s", "30 s", "350 MB".
        m = re.match(r"^([\d.]+)\s*(ms|s|MB|KB)$", budget_cell)
        if not m:
            continue
        value = float(m.group(1))
        unit = m.group(2)
        if unit == "ms":
            out[name] = value
        elif unit == "s":
            out[name] = value * 1000
        elif unit == "KB":
            out[name] = value * 1024
        elif unit == "MB":
            out[name] = value * 1024 * 1024
    return out


def test_perf_budgets_match_spec():
    """The PERF_BUDGETS dict in ems/perf.py must agree with docs/perf-budgets.md.
    This guards against drift between code and documentation."""
    from ems.perf import PERF_BUDGETS
    spec = _parse_spec_budgets()
    # Every spec budget must be present in the code dict.
    assert set(spec.keys()).issubset(set(PERF_BUDGETS.keys())), (
        f"PERF_BUDGETS is missing entries from docs/perf-budgets.md: "
        f"{set(spec.keys()) - set(PERF_BUDGETS.keys())}"
    )
    # Values must match exactly (within float tolerance).
    for name, spec_value in spec.items():
        code_value = PERF_BUDGETS[name]
        assert abs(code_value - spec_value) < 1e-6, (
            f"{name}: code={code_value} != spec={spec_value}"
        )


def test_perf_middleware_is_pure_asgi():
    """The middleware must be a pure-ASGI class, not BaseHTTPMiddleware.
    Mirrors the auth-slice invariant: BaseHTTPMiddleware wraps each request
    in an anyio task group that starves the override control cycle."""
    from starlette.middleware.base import BaseHTTPMiddleware

    assert not issubclass(PerfTimingMiddleware, BaseHTTPMiddleware), (
        "PerfTimingMiddleware must be pure ASGI; BaseHTTPMiddleware starves the "
        "override control cycle. See auth-slice invariant."
    )
    # Pure ASGI classes are callable objects with __call__(scope, receive, send).
    assert callable(PerfTimingMiddleware)
    # Constructor signature: PerfTimingMiddleware(app).
    sentinel_app = object()
    m = PerfTimingMiddleware(sentinel_app)  # type: ignore[arg-type]
    assert m.app is sentinel_app


def test_over_budget_api_logs_warn():
    """A slow H-tier request must record an over-budget sample and surface it via diagnostics."""
    REGISTRY.reset()
    app = FastAPI()
    app.add_middleware(PerfTimingMiddleware)

    @app.get("/api/status")
    async def slow_status():
        import asyncio
        # Block just past the 500 ms H-tier budget. Using sleep so the
        # middleware sees real wall-clock duration.
        await asyncio.sleep(0.6)
        return {"ok": True}

    with TestClient(app) as client:
        r = client.get("/api/status")
        assert r.status_code == 200
        # Registry must show the over-budget sample.
        s = REGISTRY.summarize("api.hot")
        assert s["n"] == 1
        assert s["over_budget_count"] == 1
        assert s["max_ms"] >= 500
        # Last overrun must reference the path template.
        overruns = REGISTRY.last_overruns()
        assert overruns, "expected at least one overrun entry"
        assert overruns[-1]["name"] == "api.hot"
        assert overruns[-1].get("path_template") == "/api/status"
