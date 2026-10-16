"""In-process SQLite writer stress (F-45 / #311). Hermetic: tmp dir, no network, no hardware.

The 30 s acceptance run is ``EMS_LOCK_STRESS_SECONDS=30``. The default unit run is shorter
so the suite stays fast; both call the same scenario.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.storage.audit import AuditStore
from ems.storage.cache import CacheStore
from ems.storage.control_state import ControlStateStore
from ems.storage.history import HistoryStore
from ems.storage.sqlite_guard import WriterGate, reset_lock_stats, wal_checkpoint_truncate


def _p99(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, math.ceil(0.99 * len(ordered)) - 1))
    return ordered[index]


def _run_stress(db_path: str, duration_s: float) -> float:
    reset_lock_stats()
    history = HistoryStore(db_path)
    audit = AuditStore(db_path)
    cache = CacheStore(db_path)
    control = ControlStateStore(db_path)
    latencies: list[float] = []
    locked = {"phrases": []}

    class _Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            if "database is locked" in message or "database is busy" in message:
                locked["phrases"].append(message)

    handler = _Catch()
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.INFO)
    try:
        async def scenario() -> None:
            await history.init()
            await audit.init()
            await asyncio.to_thread(cache.init)
            await asyncio.to_thread(control.init)
            deadline = time.monotonic() + duration_s
            seq = 0

            async def stamp() -> str:
                nonlocal seq
                seq += 1
                return f"2026-06-28T12:00:{seq % 60:02d}.{seq:06d}+00:00"

            async def timed(coro) -> None:
                started = time.perf_counter()
                await coro
                latencies.append(time.perf_counter() - started)

            raw = RawSample(
                grid_power_w=1, solar_power_w=0, battery_power_w=0, ev_power_w=0, soc_pct=50)

            async def samples() -> None:
                while time.monotonic() < deadline:
                    ts = await stamp()
                    await timed(history.record(ts, raw, reconstruct(raw)))

            async def plans() -> None:
                while time.monotonic() < deadline:
                    ts = await stamp()
                    await timed(history.record_plan(ts, {"strategy": "summer", "soc_pct": 50}))

            async def gas() -> None:
                while time.monotonic() < deadline:
                    ts = await stamp()
                    await timed(history.record_gas(ts, 1000.0))

            async def carbon() -> None:
                while time.monotonic() < deadline:
                    ts = await stamp()
                    await timed(history.upsert_carbon([(ts, 0.2)]))

            async def finance() -> None:
                n = 0
                while time.monotonic() < deadline:
                    n += 1
                    day = f"2026-01-{(n % 28) + 1:02d}"
                    await timed(history.upsert_daily_finance(day, {"calc_v": 1, "n": n}))

            async def audits() -> None:
                while time.monotonic() < deadline:
                    ts = await stamp()
                    await timed(audit.append(ts, "config_change", "stress", {}))

            async def caches() -> None:
                n = 0
                while time.monotonic() < deadline:
                    n += 1
                    await timed(asyncio.to_thread(cache.set, f"k{n}", "v", 60))

            async def states() -> None:
                n = 0
                while time.monotonic() < deadline:
                    n += 1
                    await timed(asyncio.to_thread(
                        control.save, {"switches_today": n, "last_command_unconfirmed": False}))

            async def checkpoint() -> None:
                while time.monotonic() < deadline:
                    await asyncio.to_thread(wal_checkpoint_truncate, db_path)
                    await asyncio.sleep(0)

            await asyncio.gather(
                samples(), plans(), gas(), carbon(), finance(), audits(), caches(), states(),
                checkpoint(),
            )

        asyncio.run(scenario())
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    assert locked["phrases"] == []
    assert latencies, "stress produced no writes"
    return _p99(latencies)


def test_parallel_writers_checkpoint_stress(tmp_path, capsys):
    """8 writers + WAL checkpoint. Default window is short; AC 1 uses 30 s via the env var."""
    duration = float(os.environ.get("EMS_LOCK_STRESS_SECONDS", "2"))
    p99 = _run_stress(str(tmp_path / "ems.sqlite"), duration)
    line = f"LOCK_STRESS duration_s={duration} p99_s={p99:.4f}"
    print(line)
    logging.getLogger("ems.storage").warning("%s", line)
    captured = capsys.readouterr()
    assert f"p99_s={p99:.4f}" in captured.out
    assert p99 < 1.0


def test_critical_writer_runs_ahead_of_a_waiting_noncritical():
    gate = WriterGate()
    order: list[str] = []
    gate.acquire("holder")

    def _noncritical() -> None:
        gate.acquire("noncritical")
        order.append("noncritical")
        gate.release()

    def _critical() -> None:
        gate.acquire("critical", critical=True)
        order.append("critical")
        gate.release()

    import threading
    first = threading.Thread(target=_noncritical)
    second = threading.Thread(target=_critical)
    first.start()
    time.sleep(0.05)
    second.start()
    time.sleep(0.05)
    gate.release()
    first.join(2)
    second.join(2)
    assert order[0] == "critical"
