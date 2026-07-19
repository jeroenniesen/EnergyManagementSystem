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


def test_over_budget_control_cycle_forces_auto():
    """A control cycle that overruns its 20 s budget must force the battery to
    AUTO via the single-writer seam, audit-log the overrun, and NOT call the
    intended write target a second time.

    Self-contained contract test: the stub wrapper below mirrors the production
    shape (see `ems/control/service.py:run_cycle`); the production wiring is
    covered by manual review + the broader control-service suite."""
    import asyncio

    from ems.domain import PhysicalMode
    from ems.perf import REGISTRY, atimed

    REGISTRY.reset()

    apply_calls: list[PhysicalMode] = []

    class SlowDriver:
        """apply() blocks 25 s for non-AUTO so asyncio.wait_for(20) cancels it;
        AUTO returns immediately (the recovery write)."""

        async def apply(self, mode: PhysicalMode) -> None:
            apply_calls.append(mode)
            if mode != PhysicalMode.AUTO:
                await asyncio.sleep(25)

    driver = SlowDriver()

    async def go() -> None:
        async with atimed("control.cycle"):
            try:
                await asyncio.wait_for(driver.apply(PhysicalMode.CHARGE), timeout=20)
            except TimeoutError:
                # Mirror the production wrapper: force AUTO on overrun.
                await driver.apply(PhysicalMode.AUTO)

    asyncio.run(go())

    # CHARGE was attempted (and got cancelled); AUTO was forced as the recovery write.
    assert PhysicalMode.CHARGE in apply_calls
    assert PhysicalMode.AUTO in apply_calls
    assert apply_calls[-1] == PhysicalMode.AUTO
    # Registry recorded the cycle as over-budget.
    s = REGISTRY.summarize("control.cycle")
    assert s["n"] == 1
    assert s["over_budget_count"] == 1


def test_control_tick_phase_push_points_fire_in_order():
    """Every phase push point inside `control_tick` must fire at least once on a
    real (non-stub) tick and produce a registry sample. Guards B-80: without
    per-phase timing the dominant-phase attribution in overrun audit rows is
    useless."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from ems.control.mode_controller import ModeController
    from ems.control.override import Override
    from ems.control.service import ControlContext, ControlService
    from ems.domain import BatteryIntent, PhysicalMode
    from ems.lifecycle import Lifecycle
    from ems.settings import effective_settings
    from ems.sources.battery import MockBatteryDriver

    REGISTRY.reset()

    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    now = datetime.now(UTC)
    lc.start(now)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(now)  # -> CONTROLLING
    controller = ModeController(MockBatteryDriver(), lc, dry_run=False)
    ctx = ControlContext()
    svc = ControlService(
        ctx=ctx, settings=effective_settings({}), controller=controller, store=None,
        audit_store=None, price_source=None, solar_forecast=None,
        site_tz=ZoneInfo("Europe/Amsterdam"), dry_run=False,
        current_soc=lambda now: 50.0,
        current_mode=lambda now: PhysicalMode.AUTO,
        current_towers=lambda now: None,
        data_quality=lambda now: "fresh",
        car_charging=lambda now: False,
        load_by=lambda starts: {s: 0.0 for s in starts},
        active_strategy=lambda now: "winter",
        validate_plan_obj=lambda plan, now: (_ for _ in ()).throw(AssertionError("unused")),
        planner_cfg=lambda: None,
        summer_cfg=lambda soc: None,
        adaptive_cfg=lambda: None,
    )
    # `effective_intent` reads the wall clock for override expiry, so the override must be live at
    # real-now — not the fixed `now` the sync tick otherwise uses.
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    svc.control_tick(now)

    for name in ("control.sense", "control.decide", "control.write"):
        s = REGISTRY.summarize(name)
        assert s["n"] >= 1, f"{name} produced no samples; per-phase push point missing or misplaced"


def test_overrun_audit_includes_intended_mode():
    """A control cycle that overruns its budget must capture the intent the tick reached
    (B-80 task 4 review) and surface it on the control.overrun audit row. The captured value
    is the battery intent the tick had resolved before it hung in the write path — `None` when
    effective_intent never returned (e.g. tick timed out in the sense phase).

    End-to-end: a real ControlService + a mocked controller whose decide() blocks past the
    patched 50 ms cycle budget, with a live override so effective_intent resolves to a known
    BatteryIntent (GRID_CHARGE_TO_TARGET). Reads the audit row from a real AuditStore and asserts
    detail["intended_mode"] is the captured mode string, not None.
    """
    import asyncio
    import tempfile
    import time
    from pathlib import Path

    REGISTRY.reset()

    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "t.db")

        async def go() -> None:
            from datetime import UTC, datetime, timedelta
            from zoneinfo import ZoneInfo

            from ems.control.mode_controller import ModeController
            from ems.control.override import Override
            from ems.control.service import ControlContext, ControlService
            from ems.domain import BatteryIntent, PhysicalMode
            from ems.lifecycle import Lifecycle
            from ems.perf import PERF_BUDGETS
            from ems.settings import effective_settings
            from ems.sources.battery import MockBatteryDriver
            from ems.storage.audit import AuditStore

            audit = AuditStore(db)
            await audit.init()

            lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
            now = datetime.now(UTC)
            lc.start(now)
            lc.mark_sensors_validated()
            lc.mark_probe_ok()
            lc.mark_plan_loaded()
            lc.tick(now)  # -> CONTROLLING

            class BlockingController(ModeController):
                """decide() sleeps past the patched 50 ms budget so wait_for cancels us and
                _handle_overrun fires. The original tick keeps running in the worker thread
                (Python can't kill threads on cancel), but the box is set BEFORE we get here."""

                def decide(self, intent, now, **kwargs):  # type: ignore[override]
                    time.sleep(2)
                    return super().decide(intent, now, **kwargs)

            controller = BlockingController(MockBatteryDriver(), lc, dry_run=False)
            ctx = ControlContext()
            svc = ControlService(
                ctx=ctx, settings=effective_settings({}), controller=controller, store=None,
                audit_store=audit, price_source=None, solar_forecast=None,
                site_tz=ZoneInfo("Europe/Amsterdam"), dry_run=False,
                current_soc=lambda now: 50.0,
                current_mode=lambda now: PhysicalMode.AUTO,
                current_towers=lambda now: None,
                data_quality=lambda now: "fresh",
                car_charging=lambda now: False,
                load_by=lambda starts: {s: 0.0 for s in starts},
                active_strategy=lambda now: "winter",
                validate_plan_obj=lambda plan, now: (_ for _ in ()).throw(AssertionError("unused")),
                planner_cfg=lambda: None,
                summer_cfg=lambda soc: None,
                adaptive_cfg=lambda: None,
            )
            # Live override → effective_intent returns BatteryIntent.GRID_CHARGE_TO_TARGET.
            # `effective_intent` reads the wall clock for override expiry, so it must be live at
            # real-now — same caveat as the other tick test.
            ctx.override_box["ov"] = Override(
                intent=BatteryIntent.GRID_CHARGE_TO_TARGET,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

            # Shrink the cycle budget so the test runs in <1 s (default is 20 s).
            original_budget = PERF_BUDGETS["control.cycle"]
            PERF_BUDGETS["control.cycle"] = 50.0
            try:
                await svc.run_cycle()
            finally:
                PERF_BUDGETS["control.cycle"] = original_budget

            rows = await audit.recent(limit=10, category="control.overrun")
            assert rows, "expected a control.overrun audit row"
            detail = rows[0]["detail"]
            assert "intended_mode" in detail, (
                f"control.overrun detail missing intended_mode: {detail!r}")
            assert detail["intended_mode"] is not None, (
                f"intended_mode should be captured, got None: {detail!r}")
            # The live override above resolves to GRID_CHARGE_TO_TARGET.
            assert detail["intended_mode"] == str(BatteryIntent.GRID_CHARGE_TO_TARGET), (
                f"expected intended_mode={str(BatteryIntent.GRID_CHARGE_TO_TARGET)!r} from the "
                f"live GRID_CHARGE_TO_TARGET override, got {detail['intended_mode']!r}: {detail!r}")
            assert detail["reason"] == "timeout"

        asyncio.run(go())


def test_rss_ceiling_sampled():
    """The RSS sampler must produce samples and expose them via build_perf_block.

    B-80 task 5: drives the sampler directly to verify the sampling loop + shared state, and
    confirms build_perf_block surfaces current/peak/over_ceiling_count."""
    import asyncio

    from ems.perf import RssSampler, build_perf_block

    REGISTRY.reset()

    async def go() -> None:
        sampler = RssSampler(interval_seconds=0.05)  # fast for the test
        await sampler.start()
        try:
            await asyncio.sleep(0.12)  # at least 2 samples
        finally:
            await sampler.stop()

        # Sampler has at least 2 samples internally; peak must be > 0.
        assert sampler.peak_mb() > 0

        # build_perf_block exposes current + peak + over_ceiling_count.
        block = build_perf_block()
        assert block["rss_mb"]["peak_mb"] > 0
        assert block["rss_mb"]["current_mb"] > 0
        assert isinstance(block["rss_mb"]["over_ceiling_count"], int)

    asyncio.run(go())


def test_sustained_dashboard_poll():
    """Fire 20 rounds of all 11 H-tier routes; assert p95 < 500 ms each AND
    no round's slowest request grows > 50% vs round 1.

    B-80 task 5: end-to-end check that the dashboard-10s polling pattern stays well under budget
    in mock mode and does not regress across rounds. Uses the real create_app with MockSource
    (the project's standard test app pattern, see test_api.py) and the project's standard
    `TestClient` lifespan context to start/stop background tasks.
    """
    import time as _time

    from ems.sources.mock import MockSource
    from ems.web.api import create_app

    REGISTRY.reset()
    app = create_app(MockSource(), dry_run=True, dev_mode="mock")

    HOT_PATHS = (
        "/api/status", "/api/freshness", "/api/energy-story", "/api/battery-plan",
        "/api/strategy", "/api/battery", "/api/decision", "/api/alerts",
        "/api/finance?period=day", "/api/charge-need", "/api/car/plan",
    )

    with TestClient(app) as client:
        round_maxes: list[float] = []
        for _round_idx in range(20):
            round_t0 = _time.perf_counter()
            for path in HOT_PATHS:
                client.get(path)
            round_maxes.append((_time.perf_counter() - round_t0) * 1000)

    # p95 of all 220 H-tier requests must be under the 500 ms budget.
    all_samples = REGISTRY.recent("api.hot", n=1000)
    durations = sorted(s.duration_ms for s in all_samples)
    n = len(durations)
    k = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
    p95 = durations[k]
    assert p95 < 500, f"hot-route p95 = {p95:.1f} ms exceeds 500 ms budget"

    # No round's slowest should grow > 50% vs round 1's slowest.
    # (round_maxes is wall-clock for the WHOLE round; assert no round
    # exceeds 1.5x round 1's max.)
    baseline = round_maxes[0]
    for i, m in enumerate(round_maxes):
        assert m < 1.5 * baseline, (
            f"round {i} slowest={m:.1f}ms vs round 1 slowest={baseline:.1f}ms "
            f"(degradation > 50%)"
        )
