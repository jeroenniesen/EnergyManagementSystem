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
