"""The dashboard polls several endpoints every few seconds. Each used to read the battery cluster
on EVERY request (/api/status, /api/charge-need, /api/battery), so an open dashboard sent a flood of
reads to the Indevolt towers — enough to knock a tower off the network. These tests pin the fix:
all three share a single coalesced read window, so the hardware is polled at most a fixed, small
number of times per window no matter how many requests (or browser tabs) arrive."""
import asyncio
import time
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.indevolt import TowerReading
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


class _CountingCluster:
    """Stands in for the live IndevoltClusterReader: counts how many times the towers are actually
    polled. read_power_soc() goes through read_towers() exactly like the real cluster reader, so a
    plain source.read() also registers as one tower poll (mirrors production)."""

    def __init__(self) -> None:
        self.tower_reads = 0

    def read_towers(self):
        self.tower_reads += 1
        return [
            TowerReading("10.0.0.1", 50.0, 0.0, 5.4, "master", True),
            TowerReading("10.0.0.2", 52.0, 0.0, 5.4, "slave", True),
        ]

    def read_power_soc(self):
        online = [t for t in self.read_towers() if t.online and t.soc_pct is not None]
        return sum(t.power_w for t in online), sum(t.soc_pct for t in online) / len(online)


class _CountingSource:
    """A live-shaped source: .read() returns a RawSample (and, like LiveSource, reads the battery
    cluster while doing so) and .battery exposes the per-tower reader."""

    def __init__(self) -> None:
        self.reads = 0
        self.battery = _CountingCluster()

    def read(self) -> RawSample:
        self.reads += 1
        power, soc = self.battery.read_power_soc()
        return RawSample(grid_power_w=0.0, solar_power_w=0.0, battery_power_w=power,
                         ev_power_w=0.0, soc_pct=soc)


def _app(tmp_path, source):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        source, dry_run=True, dev_mode="live", tz=AMS, store=HistoryStore(db),
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
    )


def test_hot_endpoints_share_one_coalesced_device_read_window(tmp_path):
    src = _CountingSource()
    # No `with` -> no lifespan/background loops -> the read counts come only from our requests.
    client = TestClient(_app(tmp_path, src))

    for _ in range(5):  # simulate ~5 dashboard refreshes landing inside one coalesce window
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/battery").status_code == 200
        assert client.get("/api/charge-need").status_code == 200

    # Without coalescing this would be ~15 sample reads + ~15 tower polls. With it: the sample is
    # read once (shared by /api/status + /api/charge-need) and the tower list once (for
    # /api/battery) — at most one of each per window, plus the tower poll the sample read triggers.
    assert src.reads == 1, f"sample read not coalesced: {src.reads} reads"
    assert src.battery.tower_reads <= 2, f"tower poll not coalesced: {src.battery.tower_reads}"


def test_status_and_battery_still_return_live_values_through_the_cache(tmp_path):
    # Coalescing must not blank the data — the cached read still feeds every card.
    src = _CountingSource()
    client = TestClient(_app(tmp_path, src))
    status = client.get("/api/status").json()
    assert status["soc_pct"] == 51.0  # mean of the two towers
    battery = client.get("/api/battery").json()
    assert len(battery["towers"]) == 2
    assert battery["aggregate"]["online_towers"] == 2


class _SlowCountingSource(_CountingSource):
    # A read slow enough that concurrent cold requests genuinely overlap on the cache miss — the
    # exact moment single-flight must serialise them.
    def read(self) -> RawSample:
        time.sleep(0.05)
        return super().read()


def test_concurrent_cold_reads_are_single_flight(tmp_path):
    # The highest-risk moment (cold start / cache expiry with several tabs): many requests hit the
    # empty cache at once. Single-flight must collapse them to ONE hardware read, not one per
    # request — this is what stops a read flood knocking an Indevolt tower offline.
    src = _SlowCountingSource()
    app = _app(tmp_path, src)

    async def go():
        # ASGITransport drives the app in-process; sync endpoints run in the threadpool, so 20
        # gathered requests truly race on the cold cache (and the single-flight lock).
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            src.reads = 0
            src.battery.tower_reads = 0
            return await asyncio.gather(*[c.get("/api/status") for _ in range(20)])

    results = asyncio.run(go())
    assert all(r.status_code == 200 for r in results)
    assert src.reads == 1, f"single-flight failed: {src.reads} concurrent hardware reads"
