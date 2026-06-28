import asyncio
from datetime import UTC, datetime

from ems.control.loop import ControlLoop


def test_control_loop_ticks_until_stopped():
    calls: list[datetime] = []

    async def run():
        stop = asyncio.Event()
        loop = ControlLoop(calls.append, cycle_seconds=0.01)
        task = asyncio.create_task(loop.run(stop))
        await asyncio.sleep(0.06)
        stop.set()
        await task

    asyncio.run(run())
    assert len(calls) >= 2  # ticked repeatedly on its cycle


def test_control_loop_survives_a_failing_tick():
    n = {"c": 0}

    def boom(_now):
        n["c"] += 1
        raise RuntimeError("transient")

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(ControlLoop(boom, cycle_seconds=0.01).run(stop))
        await asyncio.sleep(0.06)
        stop.set()
        await task

    asyncio.run(run())
    assert n["c"] >= 2  # a failing tick is logged, never kills the loop (fail-safe)


def test_control_loop_stops_promptly():
    async def run():
        stop = asyncio.Event()
        loop = ControlLoop(lambda _n: None, cycle_seconds=999, clock=lambda: datetime.now(UTC))
        task = asyncio.create_task(loop.run(stop))
        stop.set()  # should return on the next wait, not after 999s
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
