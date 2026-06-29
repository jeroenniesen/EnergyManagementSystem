import asyncio

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.storage.history import HistoryStore


def _raw(grid, soc=50.0):
    return RawSample(
        grid_power_w=grid, solar_power_w=0, battery_power_w=0, ev_power_w=0, soc_pct=soc
    )


def test_record_and_read_recent(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        raw = RawSample(
            grid_power_w=200, solar_power_w=0, battery_power_w=800, ev_power_w=0, soc_pct=55
        )
        await store.record("2026-06-27T10:00:00+02:00", raw, reconstruct(raw))
        return await store.recent_raw(10), await store.recent_derived(10)

    rraw, rder = asyncio.run(run())
    assert len(rraw) == 1
    assert rraw[0]["grid_power_w"] == 200
    assert rraw[0]["soc_pct"] == 55
    assert rraw[0]["ts"] == "2026-06-27T10:00:00+02:00"
    assert len(rder) == 1
    assert rder[0]["house_load_w"] == 1000
    assert rder[0]["non_ev_load_w"] == 1000


def test_raw_and_derived_in_separate_tables(tmp_path):
    # SPEC §4.3: raw vs derived stored separately so values can be re-derived.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        return await store.table_names()

    names = asyncio.run(run())
    assert "raw_samples" in names
    assert "derived_samples" in names


def test_recent_is_newest_first_and_limited(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        for i, ts in enumerate(
            ["2026-06-27T10:00:00+02:00", "2026-06-27T10:05:00+02:00", "2026-06-27T10:10:00+02:00"]
        ):
            raw = _raw(i)
            await store.record(ts, raw, reconstruct(raw))
        return await store.recent_raw(2)

    rows = asyncio.run(run())
    assert len(rows) == 2
    assert rows[0]["ts"] == "2026-06-27T10:10:00+02:00"  # newest first
    assert rows[1]["ts"] == "2026-06-27T10:05:00+02:00"


def test_purge_older_than_deletes_both_tables_atomically(tmp_path):
    # Retention must drop old rows from raw AND derived together (never leave them out of sync).
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        for ts in ["2026-06-01T00:00:00+00:00",  # old (purged)
                   "2026-06-02T00:00:00+00:00",  # old (purged)
                   "2026-06-28T00:00:00+00:00"]:  # kept
            raw = _raw(1)
            await store.record(ts, raw, reconstruct(raw))
        deleted = await store.purge_older_than("2026-06-10T00:00:00+00:00")
        return deleted, await store.recent_raw(10), await store.recent_derived(10)

    deleted, rraw, rder = asyncio.run(run())
    assert deleted == 4  # 2 raw + 2 derived
    assert len(rraw) == 1 and len(rder) == 1  # only the kept row remains in both tables
    assert rraw[0]["ts"] == "2026-06-28T00:00:00+00:00"


def test_maintain_and_db_stats(tmp_path):
    # maintain() must not raise (checkpoint + incremental vacuum), and db_stats reports row counts.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        raw = _raw(1)
        await store.record("2026-06-28T00:00:00+00:00", raw, reconstruct(raw))
        await store.maintain()  # must be a no-op-safe success
        return await store.db_stats()

    stats = asyncio.run(run())
    assert stats["raw_rows"] == 1 and stats["derived_rows"] == 1
    assert stats["db_bytes"] > 0 and stats["wal_bytes"] >= 0
