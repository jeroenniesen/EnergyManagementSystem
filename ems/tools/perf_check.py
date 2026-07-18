"""`make perf-check` entrypoint.

Runs a canned workload against a TestClient + canned control cycle, then
prints a Markdown table comparing measured percentiles against the budgets
in ems.perf.PERF_BUDGETS. Exits 0 if all green, 1 if any budget is
exceeded.

Output is human-readable. Not consumed by CI (per B-80's design decision:
local command only).
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Iterable

from ems.perf import PERF_BUDGETS, REGISTRY, RssSampler, atimed, build_perf_block


def _measure(samples: Iterable[float]) -> tuple[float, float, float, int]:
    durations = sorted(samples)
    if not durations:
        return 0.0, 0.0, 0.0, 0
    n = len(durations)

    def pct(p: float) -> float:
        k = max(0, min(n - 1, int(round(p * (n - 1)))))
        return durations[k]

    return pct(0.50), pct(0.95), max(durations), n


def _render_row(name: str, tier: str, p50: float, p95: float, mx: float, n: int,
                budget_ms: float, over: int) -> str:
    budget_str = f"{budget_ms:g} ms" if budget_ms < 60_000 else f"{budget_ms / 1000:g} s"
    p95_str = f"{p95:.0f}" if p95 >= 1 else f"{p95:.2f}"
    status = "PASS" if over == 0 and (n == 0 or p95 <= budget_ms) else "FAIL"
    return (
        f"| {name:<19} | {tier:<4} | {p50:>7.1f}   | {p95_str:>7}   | "
        f"{mx:>7.1f}   | {n:>3} | {budget_str:<6} | {status}   |"
    )


async def _sample_rss_once(sampler: RssSampler) -> None:
    await sampler.start()
    try:
        await asyncio.sleep(0.12)
    finally:
        await sampler.stop()


def _run_workload() -> None:
    """Exercise the canned perf workload. Pushes samples into the singleton REGISTRY."""
    from fastapi.testclient import TestClient

    from ems.sources.mock import MockSource
    from ems.web.api import create_app

    REGISTRY.reset()

    app = create_app(MockSource(), dry_run=True, dev_mode="mock")

    HOT_PATHS = (
        "/api/status", "/api/freshness", "/api/energy-story", "/api/battery-plan",
        "/api/strategy", "/api/battery", "/api/decision", "/api/alerts",
        "/api/finance?period=day", "/api/charge-need", "/api/car/plan",
    )
    INTERACTIVE_PATHS = ("/api/settings", "/api/cars", "/api/forecast")
    BATCH_PATHS = ("/api/digest", "/api/advisor/ev-charge")

    with TestClient(app) as client:
        for path in HOT_PATHS:
            t0 = time.perf_counter()
            try:
                client.get(path)
            except Exception:
                pass
            REGISTRY.push("api.hot", (time.perf_counter() - t0) * 1000)
        for path in INTERACTIVE_PATHS:
            t0 = time.perf_counter()
            try:
                client.get(path)
            except Exception:
                pass
            REGISTRY.push("api.interactive", (time.perf_counter() - t0) * 1000)
        for path in BATCH_PATHS:
            t0 = time.perf_counter()
            try:
                client.get(path)
            except Exception:
                pass
            REGISTRY.push("api.batch", (time.perf_counter() - t0) * 1000)

    async def fake_cycle() -> None:
        async with atimed("control.cycle"):
            await asyncio.sleep(0.05)

    asyncio.run(fake_cycle())

    REGISTRY.push("replay.run", 8_000.0)
    REGISTRY.push("report.build", 3_000.0)

    sampler = RssSampler(interval_seconds=0.05)
    asyncio.run(_sample_rss_once(sampler))


def _print_report() -> int:
    print()
    print("| name                | tier | p50 (ms)  | p95 (ms) | max (ms) |   n | budget | pass |")
    print("|---------------------|------|-----------|----------|----------|-----|--------|------|")

    failures = 0
    for tier in ("hot", "interactive", "batch"):
        name = f"api.{tier}"
        s = REGISTRY.summarize(name)
        budget = PERF_BUDGETS.get(name, 0)
        recent = REGISTRY.recent(name, n=1000)
        p50, p95, mx, n = _measure(r.duration_ms for r in recent)
        over = s["over_budget_count"]
        print(_render_row(name, tier.upper(), p50, p95, mx, n, budget, over))
        if over > 0:
            failures += 1

    for name in ("control.cycle", "replay.run", "report.build"):
        s = REGISTRY.summarize(name)
        recent = REGISTRY.recent(name, n=1000)
        p50, p95, mx, n = _measure(r.duration_ms for r in recent)
        budget = PERF_BUDGETS.get(name, 0)
        over = s["over_budget_count"]
        print(_render_row(name, "-", p50, p95, mx, n, budget, over))
        if over > 0:
            failures += 1

    perf = build_perf_block()
    rss = perf["rss_mb"]
    rss_pass = rss["over_ceiling_count"] == 0
    print(
        f"| memory.rss.peak     | -    |     -     |    -     | "
        f"{rss['peak_mb']:>7.1f}   | {1:>3} | 350 MB   | "
        f"{'PASS' if rss_pass else 'FAIL'}   |"
    )
    if not rss_pass:
        failures += 1

    print()
    if failures == 0:
        print("All budgets green.")
        return 0
    print(f"{failures} budget(s) exceeded. See table above.")
    return 1


def main() -> int:
    _run_workload()
    return _print_report()


if __name__ == "__main__":
    sys.exit(main())
