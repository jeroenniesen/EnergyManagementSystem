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


def test_store_wrappers_record_samples():
    """Every store hot-path method must push a sample into the registry under its
    store.*.read|write name. Guards B-80 per-store timing wrappers — without them
    the SQLite hot path is invisible on /api/diagnostics.perf."""
    import asyncio
    import tempfile
    from pathlib import Path

    from ems.domain import RawSample
    from ems.load_model import DerivedSample
    from ems.storage.audit import AuditStore
    from ems.storage.cache import CacheStore
    from ems.storage.control_state import ControlStateStore
    from ems.storage.history import HistoryStore
    from ems.storage.settings import SettingsStore

    REGISTRY.reset()
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "t.db")

        async def go() -> None:
            hs = HistoryStore(db)
            ss = SettingsStore(db)
            aus = AuditStore(db)
            cs = ControlStateStore(db)
            cache = CacheStore(db)

            await hs.init()
            await ss.init()
            await aus.init()
            cs.init()
            cache.init()

            # Exercise each store's hot-path method at least once. Method names below are the
            # ACTUAL public hot-path APIs — `record` (not `record_samples`), `all`/`set_many`
            # (settings has no `get`/`set`), `load`/`save` (control_state has no `get`/`set`).
            await hs.table_names()  # store.history.read
            raw = RawSample(grid_power_w=100.0, solar_power_w=0.0, battery_power_w=0.0,
                            ev_power_w=0.0, soc_pct=50.0)
            derived = DerivedSample(house_load_w=100.0, non_ev_load_w=100.0)
            await hs.record("2026-07-18T10:00:00Z", raw, derived)  # store.history.write
            # The 3 helpers _recent/_since/_between back 6 public read methods. Capture the
            # store.history.read sample count NOW (only `table_names` has produced one so far),
            # then call one method backed by each helper and assert the count grew by exactly 3.
            # If any of _recent/_since/_between were unwrapped, the count would not grow by 3.
            history_read_before = REGISTRY.summarize("store.history.read")["n"]
            await hs.recent_raw(limit=10)  # covers _recent
            await hs.recent_raw_since("2026-07-18T00:00:00Z", limit=10)  # covers _since
            await hs.raw_between("2026-07-18T00:00:00Z", "2026-07-19T00:00:00Z",
                                 limit=10)  # covers _between
            history_read_after = REGISTRY.summarize("store.history.read")["n"]
            assert history_read_after - history_read_before >= 3, (
                f"Delegation helpers not all wrapped: store.history.read grew by "
                f"{history_read_after - history_read_before}, expected >= 3 "
                f"(_recent + _since + _between)"
            )
            await ss.set_many({"x": "1"})  # store.settings.write
            await ss.all()  # store.settings.read
            await aus.append("2026-07-18T10:00:00Z", "test", "hello", {"k": 1})  # audit
            cache.set("k", "v", ttl_seconds=60)  # store.cache.set
            cache.get("k")  # store.cache.get
            cs.load()  # store.control_state.read
            cs.save({"daily_switches": 1})  # store.control_state.write

        asyncio.run(go())

        for name in ("store.history.read", "store.history.write",
                     "store.settings.read", "store.settings.write",
                     "store.audit.append",
                     "store.cache.get", "store.cache.set",
                     "store.control_state.read", "store.control_state.write"):
            s = REGISTRY.summarize(name)
            assert s["n"] >= 1, f"{name} produced no samples; wrapper missing or wrong name"
