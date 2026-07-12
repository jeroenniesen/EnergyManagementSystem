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


def test_price_slots_upsert_and_query(tmp_path):
    # Spec 2026-07-03: prices persist so finance/best-price survive beyond the live price window.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.upsert_price_slots([("2026-06-28T10:00:00+00:00", 0.20),
                                        ("2026-06-28T10:15:00+00:00", 0.25)])
        # Same slot again with a corrected price → overwrites, no duplicate row.
        await store.upsert_price_slots([("2026-06-28T10:15:00+00:00", 0.30)])
        return await store.prices_between("2026-06-28T00:00:00+00:00",
                                          "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [(r["start_ts"], r["eur_per_kwh"]) for r in rows] == [
        ("2026-06-28T10:00:00+00:00", 0.20), ("2026-06-28T10:15:00+00:00", 0.30)]


def test_daily_finance_roundtrip_and_upsert(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.upsert_daily_finance("2026-06-28", {"saved_eur": 1.0, "has_data": True})
        await store.upsert_daily_finance("2026-06-28", {"saved_eur": 1.25, "has_data": True})
        await store.upsert_daily_finance("2026-06-29", {"saved_eur": 0.4, "has_data": True})
        return await store.daily_finance_between("2026-06-28", "2026-06-30")

    rows = asyncio.run(run())
    assert [r["day"] for r in rows] == ["2026-06-28", "2026-06-29"]
    assert rows[0]["data"]["saved_eur"] == 1.25  # upsert overwrote


def test_forecast_snapshot_upsert_is_insert_or_ignore(tmp_path):
    # observability-data: the FIRST snapshot recorded for a (issued_date, slot) sticks — later
    # cycles the same day must NOT overwrite it (we want the day-ahead forecast, not a nowcast).
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.upsert_forecast_snapshot(
            "2026-06-28", [("2026-06-28T10:00:00+00:00", 100.0, 200.0, 300.0)])
        # Same (issued_date, start) again with different values → ignored, first value sticks.
        await store.upsert_forecast_snapshot(
            "2026-06-28", [("2026-06-28T10:00:00+00:00", 999.0, 999.0, 999.0)])
        return await store.forecasts_between("2026-06-28T00:00:00+00:00",
                                             "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert (rows[0]["p10_w"], rows[0]["p50_w"], rows[0]["p90_w"]) == (100.0, 200.0, 300.0)


def test_forecast_snapshot_empty_list_is_noop(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.upsert_forecast_snapshot("2026-06-28", [])
        return await store.forecasts_between("2020-01-01T00:00:00+00:00",
                                             "2030-01-01T00:00:00+00:00")

    assert asyncio.run(run()) == []


def test_forecasts_between_returns_window_ordered(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.upsert_forecast_snapshot("2026-06-28", [
            ("2026-06-28T10:00:00+00:00", 1.0, 2.0, 3.0),
            ("2026-06-28T10:15:00+00:00", 1.1, 2.1, 3.1),
        ])
        await store.upsert_forecast_snapshot("2026-06-29", [
            ("2026-06-29T10:00:00+00:00", 4.0, 5.0, 6.0),
        ])
        return await store.forecasts_between("2026-06-28T00:00:00+00:00",
                                             "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [r["start"] for r in rows] == [
        "2026-06-28T10:00:00+00:00", "2026-06-28T10:15:00+00:00"]
    assert rows[0]["issued_date"] == "2026-06-28"


def test_purge_trims_forecasts_by_start_but_keeps_recent(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.upsert_forecast_snapshot("2026-01-01", [
            ("2026-01-01T10:00:00+00:00", 1.0, 2.0, 3.0),   # old (purged)
        ])
        await store.upsert_forecast_snapshot("2026-06-28", [
            ("2026-06-28T10:00:00+00:00", 4.0, 5.0, 6.0),   # kept
        ])
        deleted = await store.purge_older_than("2026-06-01T00:00:00+00:00")
        rows = await store.forecasts_between("2020-01-01T00:00:00+00:00",
                                             "2030-01-01T00:00:00+00:00")
        return deleted, rows

    deleted, rows = asyncio.run(run())
    assert deleted >= 1
    assert [r["start"] for r in rows] == ["2026-06-28T10:00:00+00:00"]


def test_record_plan_roundtrip_via_plan_history_between(tmp_path):
    # observability-data: what the planner intended each cycle (target SoC/strategy/intent),
    # to later compare `target_soc` against the achieved `soc_pct` in raw_samples.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.record_plan("2026-06-28T10:00:00+00:00", {
            "strategy": "winter", "target_soc": 80.0,
            "deadline": "2026-06-28T18:00:00+00:00", "soc_pct": 55.0,
            "intent": "grid_charge_to_target",
        })
        await store.record_plan("2026-06-28T10:15:00+00:00", {
            "strategy": "winter", "target_soc": 80.0,
            "deadline": "2026-06-28T18:00:00+00:00", "soc_pct": 58.0,
            "intent": "grid_charge_to_target",
        })
        return await store.plan_history_between(
            "2026-06-28T00:00:00+00:00", "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [r["ts"] for r in rows] == [
        "2026-06-28T10:00:00+00:00", "2026-06-28T10:15:00+00:00"]  # oldest-first
    assert rows[0]["strategy"] == "winter"
    assert rows[0]["target_soc"] == 80.0
    assert rows[0]["deadline"] == "2026-06-28T18:00:00+00:00"
    assert rows[0]["soc_pct"] == 55.0
    assert rows[0]["intent"] == "grid_charge_to_target"
    assert rows[1]["soc_pct"] == 58.0


def test_record_plan_missing_keys_default_to_none(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.record_plan("2026-06-28T10:00:00+00:00", {})
        return await store.plan_history_between(
            "2026-06-28T00:00:00+00:00", "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy"] is None and row["target_soc"] is None
    assert row["deadline"] is None and row["soc_pct"] is None and row["intent"] is None


def test_purge_trims_plan_history_by_ts_but_keeps_recent(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.record_plan("2026-01-01T10:00:00+00:00", {"strategy": "winter"})  # purged
        await store.record_plan("2026-06-28T10:00:00+00:00", {"strategy": "summer"})  # kept
        deleted = await store.purge_older_than("2026-06-01T00:00:00+00:00")
        rows = await store.plan_history_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
        return deleted, rows

    deleted, rows = asyncio.run(run())
    assert deleted >= 1
    assert [r["ts"] for r in rows] == ["2026-06-28T10:00:00+00:00"]


def test_gas_record_and_between_roundtrip(tmp_path):
    # B-02: cumulative gas meter readings, one row/cycle when a gas meter is paired.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.record_gas("2026-06-28T10:00:00+00:00", 1000.0)
        await store.record_gas("2026-06-28T10:15:00+00:00", 1000.5)
        # Re-recording the same ts overwrites rather than duplicating.
        await store.record_gas("2026-06-28T10:15:00+00:00", 1000.6)
        return await store.gas_between("2026-06-28T00:00:00+00:00", "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [(r["ts"], r["total_gas_m3"]) for r in rows] == [
        ("2026-06-28T10:00:00+00:00", 1000.0), ("2026-06-28T10:15:00+00:00", 1000.6)]


def test_gas_between_is_windowed_and_oldest_first(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.record_gas("2026-06-01T00:00:00+00:00", 500.0)  # outside window
        await store.record_gas("2026-06-28T10:15:00+00:00", 1001.0)
        await store.record_gas("2026-06-28T10:00:00+00:00", 1000.0)
        return await store.gas_between("2026-06-28T00:00:00+00:00", "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [r["ts"] for r in rows] == ["2026-06-28T10:00:00+00:00", "2026-06-28T10:15:00+00:00"]


def test_purge_trims_gas_readings_by_ts_but_keeps_recent(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.record_gas("2026-01-01T00:00:00+00:00", 500.0)   # old (purged)
        await store.record_gas("2026-06-28T00:00:00+00:00", 1000.0)  # kept
        deleted = await store.purge_older_than("2026-06-01T00:00:00+00:00")
        rows = await store.gas_between("2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
        return deleted, rows

    deleted, rows = asyncio.run(run())
    assert deleted >= 1
    assert [r["ts"] for r in rows] == ["2026-06-28T00:00:00+00:00"]


def test_purge_trims_prices_but_keeps_daily_finance(tmp_path):
    # daily_finance is the long-horizon record (B-13) — retention must never eat it.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        raw = _raw(100.0)
        await store.record("2026-01-01T10:00:00+00:00", raw, reconstruct(raw))
        await store.upsert_price_slots([("2026-01-01T10:00:00+00:00", 0.20),
                                        ("2026-06-28T10:00:00+00:00", 0.25)])
        await store.upsert_daily_finance("2026-01-01", {"saved_eur": 0.9, "has_data": True})
        await store.purge_older_than("2026-06-01T00:00:00+00:00")
        prices = await store.prices_between("2020-01-01T00:00:00+00:00",
                                            "2030-01-01T00:00:00+00:00")
        fin = await store.daily_finance_between("2020-01-01", "2030-01-01")
        return prices, fin

    prices, fin = asyncio.run(run())
    assert [p["start_ts"] for p in prices] == ["2026-06-28T10:00:00+00:00"]
    assert [f["day"] for f in fin] == ["2026-01-01"]


def test_carbon_intensity_upsert_and_query(tmp_path):
    # Roadmap F3: time-varying grid CO2 intensity, mirrors price_slots (reporting-only).
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.upsert_carbon([("2026-06-28T10:00:00+00:00", 0.20),
                                   ("2026-06-28T10:15:00+00:00", 0.25)])
        # Same slot again with a corrected value → overwrites, no duplicate row.
        await store.upsert_carbon([("2026-06-28T10:15:00+00:00", 0.30)])
        return await store.carbon_between("2026-06-28T00:00:00+00:00",
                                          "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [(r["start_ts"], r["kg_per_kwh"]) for r in rows] == [
        ("2026-06-28T10:00:00+00:00", 0.20), ("2026-06-28T10:15:00+00:00", 0.30)]


def test_carbon_intensity_upsert_empty_list_is_noop(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.upsert_carbon([])
        return await store.carbon_between("2020-01-01T00:00:00+00:00",
                                          "2030-01-01T00:00:00+00:00")

    assert asyncio.run(run()) == []


def test_purge_trims_carbon_intensity_but_keeps_recent(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.upsert_carbon([("2026-01-01T10:00:00+00:00", 0.30),   # old (purged)
                                   ("2026-06-28T10:00:00+00:00", 0.20)])  # kept
        deleted = await store.purge_older_than("2026-06-01T00:00:00+00:00")
        rows = await store.carbon_between("2020-01-01T00:00:00+00:00",
                                          "2030-01-01T00:00:00+00:00")
        return deleted, rows

    deleted, rows = asyncio.run(run())
    assert deleted >= 1
    assert [r["start_ts"] for r in rows] == ["2026-06-28T10:00:00+00:00"]
