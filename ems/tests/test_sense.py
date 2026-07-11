import asyncio
from datetime import UTC, datetime

from ems.domain import RawSample
from ems.freshness import Freshness, FreshnessTracker
from ems.sense import SIGNALS, Recorder
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore

NOW = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)


class _BoomSource:
    def read(self):
        raise RuntimeError("boom")


class _ImplausibleBatterySource:
    """Stub reporting a gross out-of-range battery reading (sensor/comms glitch)."""

    def read(self):
        return RawSample(
            grid_power_w=200.0,
            solar_power_w=0.0,
            battery_power_w=50000.0,
            ev_power_w=0.0,
            soc_pct=55.0,
        )


class _BoomStore:
    async def record(self, *_a, **_k):
        raise RuntimeError("disk full")


def test_sense_once_records_and_marks_fresh(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker(stale_after_s=600)
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh)

    async def run():
        await store.init()
        await rec.sense_once(NOW)
        return await store.recent_raw(10)

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0]["grid_power_w"] == 200
    for sig in SIGNALS:
        assert fresh.state(sig, NOW) is Freshness.FRESH


def test_sense_once_clamps_implausible_battery_reading(tmp_path):
    # Defense-in-depth: a gross out-of-range battery reading (sensor/comms glitch) must be
    # clamped before it's stored or reconstructed, and counted for visibility.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker(stale_after_s=600)
    fresh.register(*SIGNALS)
    rec = Recorder(_ImplausibleBatterySource(), store, fresh)

    async def run():
        await store.init()
        await rec.sense_once(NOW)
        return await store.recent_raw(10)

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0]["battery_power_w"] == 20000.0
    assert rec.health()["clamped_samples"] == 1


def test_sense_once_normal_reading_does_not_clamp(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker(stale_after_s=600)
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh)

    async def run():
        await store.init()
        await rec.sense_once(NOW)

    asyncio.run(run())
    assert rec.health()["clamped_samples"] == 0


def test_record_now_writes_a_sample(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh)

    async def run():
        await store.init()
        await rec.record_now()
        return await store.recent_raw(10)

    assert len(asyncio.run(run())) == 1


def test_run_records_each_cycle_then_stops(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh, cycle_seconds=0.01)

    async def run():
        await store.init()
        stop = asyncio.Event()
        task = asyncio.create_task(rec.run(stop))
        await asyncio.sleep(0.06)  # ~several 10ms cycles
        stop.set()
        await task
        return await store.recent_raw(20)

    rows = asyncio.run(run())
    assert len(rows) >= 1  # the periodic loop recorded at least once


def test_run_survives_source_error(tmp_path):
    # Fail-safe: a raising source must not crash the recorder loop.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(_BoomSource(), store, fresh, cycle_seconds=0.01)

    async def run():
        await store.init()
        stop = asyncio.Event()
        task = asyncio.create_task(rec.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task  # must not raise
        return await store.recent_raw(10)

    rows = asyncio.run(run())
    assert rows == []  # nothing recorded, but the loop survived (task completed cleanly)


def test_run_survives_store_error():
    # Fail-safe: a store write failure (disk full, locked DB) must not crash the loop either.
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), _BoomStore(), fresh, cycle_seconds=0.01)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(rec.run(stop))
        await asyncio.sleep(0.04)
        stop.set()
        await task  # must not raise

    asyncio.run(run())  # completes cleanly == loop survived store errors


class _StubPrices:
    """Minimal price source: .slots() → objects with .start / .eur_per_kwh."""

    def __init__(self, slots):
        self._slots = slots

    def slots(self):
        return self._slots


class _BoomPrices:
    def slots(self):
        raise RuntimeError("tibber down")


def test_sense_once_persists_price_slots(tmp_path):
    # Spec 2026-07-03: each cycle upserts the current price curve so past slots keep their price.
    from ems.sources.prices import PriceSlot

    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    slots = [PriceSlot(NOW, 0.20), PriceSlot(NOW.replace(minute=15), 0.25)]
    rec = Recorder(MockSource(), store, fresh, price_source=_StubPrices(slots))

    async def run():
        await store.init()
        await rec.sense_once(NOW)
        await rec.sense_once(NOW)  # idempotent — same slots again
        return await store.prices_between("2020-01-01T00:00:00+00:00",
                                          "2030-01-01T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [(r["start_ts"], r["eur_per_kwh"]) for r in rows] == [
        (NOW.isoformat(), 0.20), (NOW.replace(minute=15).isoformat(), 0.25)]


def test_price_persist_failure_never_kills_the_cycle(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh, price_source=_BoomPrices())

    async def run():
        await store.init()
        await rec.sense_once(NOW)  # must not raise
        return await store.recent_raw(10)

    rows = asyncio.run(run())
    assert len(rows) == 1  # the sample was still recorded


class _StubForecast:
    """Minimal solar forecast source: .slots() → objects with .start / .p10_w / .p50_w / .p90_w."""

    def __init__(self, slots):
        self._slots = slots

    def slots(self):
        return self._slots


class _BoomForecast:
    def slots(self):
        raise RuntimeError("solcast down")


def test_sense_once_persists_forecast_snapshot(tmp_path):
    # observability-data: each cycle snapshots today's day-ahead solar forecast.
    from ems.sources.forecast import ForecastSlot

    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    slots = [ForecastSlot(NOW, 100.0, 200.0, 300.0),
             ForecastSlot(NOW.replace(minute=15), 110.0, 210.0, 310.0)]
    rec = Recorder(MockSource(), store, fresh, solar_forecast=_StubForecast(slots))

    async def run():
        await store.init()
        await rec.sense_once(NOW)
        return await store.forecasts_between("2020-01-01T00:00:00+00:00",
                                             "2030-01-01T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [(r["start"], r["p10_w"], r["p50_w"], r["p90_w"]) for r in rows] == [
        (NOW.isoformat(), 100.0, 200.0, 300.0),
        (NOW.replace(minute=15).isoformat(), 110.0, 210.0, 310.0),
    ]


def test_sense_once_forecast_snapshot_idempotent_same_day(tmp_path):
    # A SECOND sense_once the same UTC day must NOT change the recorded snapshot (first sticks —
    # we want the day-ahead forecast, not a later nowcast).
    from ems.sources.forecast import ForecastSlot

    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    first = [ForecastSlot(NOW, 100.0, 200.0, 300.0)]
    later = [ForecastSlot(NOW, 999.0, 999.0, 999.0)]
    forecast = _StubForecast(first)
    rec = Recorder(MockSource(), store, fresh, solar_forecast=forecast)

    async def run():
        await store.init()
        await rec.sense_once(NOW)
        forecast._slots = later  # simulate a later cycle with a changed (nowcast) forecast
        await rec.sense_once(NOW)
        return await store.forecasts_between("2020-01-01T00:00:00+00:00",
                                             "2030-01-01T00:00:00+00:00")

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert (rows[0]["p10_w"], rows[0]["p50_w"], rows[0]["p90_w"]) == (100.0, 200.0, 300.0)


def test_forecast_persist_failure_never_kills_the_cycle(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh, solar_forecast=_BoomForecast())

    async def run():
        await store.init()
        await rec.sense_once(NOW)  # must not raise
        return await store.recent_raw(10)

    rows = asyncio.run(run())
    assert len(rows) == 1  # the sample was still recorded


def test_plan_provider_defaults_to_none():
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), HistoryStore(":memory:"), fresh)
    assert rec.plan_provider is None


def test_sense_once_persists_plan_snapshot(tmp_path):
    # observability-data: each cycle snapshots the planner's target/strategy/intent so it can
    # later be compared against the achieved soc_pct in raw_samples.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh)
    rec.plan_provider = lambda now: {
        "strategy": "winter", "target_soc": 80.0,
        "deadline": now.isoformat(), "soc_pct": 55.0, "intent": "grid_charge_to_target",
    }

    async def run():
        await store.init()
        await rec.sense_once(NOW)
        return await store.plan_history_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0]["ts"] == NOW.isoformat()
    assert rows[0]["strategy"] == "winter"
    assert rows[0]["target_soc"] == 80.0
    assert rows[0]["intent"] == "grid_charge_to_target"
    assert rows[0]["soc_pct"] == 55.0


def test_plan_provider_returning_none_writes_nothing(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh)
    rec.plan_provider = lambda now: None  # e.g. no plan yet (no price source)

    async def run():
        await store.init()
        await rec.sense_once(NOW)
        return await store.plan_history_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")

    assert asyncio.run(run()) == []


def test_plan_provider_failure_never_kills_the_cycle(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh)

    def _boom(now):
        raise RuntimeError("planner blew up")

    rec.plan_provider = _boom

    async def run():
        await store.init()
        await rec.sense_once(NOW)  # must not raise
        raw = await store.recent_raw(10)
        plan = await store.plan_history_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
        return raw, plan

    raw, plan = asyncio.run(run())
    assert len(raw) == 1  # the sample was still recorded
    assert plan == []
