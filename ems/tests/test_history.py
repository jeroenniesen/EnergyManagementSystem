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


def test_backup_to_produces_valid_snapshot(tmp_path):
    # SPEC §11 durability: an online VACUUM INTO backup creates its parent dir on demand, returns
    # the byte size, and is a valid, independent SQLite file whose row counts match the source.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    dest = tmp_path / "backups" / "snap.sqlite"  # parent dir does NOT exist yet

    async def run():
        await store.init()
        for i, ts in enumerate(["2026-06-27T10:00:00+00:00", "2026-06-27T10:05:00+00:00",
                                 "2026-06-27T10:10:00+00:00"]):
            raw = _raw(i)
            await store.record(ts, raw, reconstruct(raw))
        return await store.backup_to(str(dest))

    size = asyncio.run(run())
    assert dest.exists()  # parent dir was created on demand
    assert size == dest.stat().st_size > 0  # returned size matches the real file

    import sqlite3

    con = sqlite3.connect(str(dest))
    try:
        assert con.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM derived_samples").fetchone()[0] == 3
    finally:
        con.close()


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


def test_record_plan_roundtrips_commitment_columns(tmp_path):
    # plan_version + floor_soc (the intent-aware follow-through scorer's inputs) round-trip when a
    # snapshot carries them; a snapshot without them still stores None (backward compatible).
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.record_plan("2026-06-28T10:00:00+00:00", {
            "strategy": "summer", "target_soc": 55.0, "deadline": "2026-06-28T21:00:00+00:00",
            "soc_pct": 40.0, "intent": "discharge_for_load",
            "plan_version": "epoch-7", "floor_soc": 10.0,
        })
        await store.record_plan("2026-06-28T10:15:00+00:00", {"strategy": "summer"})  # no commit
        return await store.plan_history_between(
            "2026-06-28T00:00:00+00:00", "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert rows[0]["plan_version"] == "epoch-7"
    assert rows[0]["floor_soc"] == 10.0
    assert rows[1]["plan_version"] is None and rows[1]["floor_soc"] is None


def test_plan_history_gains_commitment_columns_on_an_existing_db(tmp_path):
    # An EXISTING db whose plan_history predates the commitment columns must gain them on init
    # (idempotent ALTER) without dropping its legacy rows — and those legacy rows read back with
    # plan_version/floor_soc = None so the scorer's fallback path handles them.
    import aiosqlite
    path = str(tmp_path / "ems.sqlite")

    async def run():
        async with aiosqlite.connect(path) as db:  # pre-migration shape (raw_samples => "existing")
            await db.execute(
                "CREATE TABLE raw_samples (ts TEXT NOT NULL, grid_power_w REAL NOT NULL, "
                "solar_power_w REAL NOT NULL, battery_power_w REAL NOT NULL, "
                "ev_power_w REAL NOT NULL, soc_pct REAL NOT NULL)")
            await db.execute(
                "CREATE TABLE derived_samples (ts TEXT NOT NULL, house_load_w REAL NOT NULL, "
                "non_ev_load_w REAL NOT NULL)")
            await db.execute(
                "CREATE TABLE plan_history (ts TEXT NOT NULL, strategy TEXT, target_soc REAL, "
                "deadline TEXT, soc_pct REAL, intent TEXT)")  # OLD 6-column shape
            await db.execute(
                "INSERT INTO plan_history (ts, strategy, target_soc, deadline, soc_pct, intent) "
                "VALUES ('2026-06-28T10:00:00+00:00','winter',80.0,"
                "'2026-06-28T18:00:00+00:00',55.0,'grid_charge_to_target')")
            await db.commit()

        store = HistoryStore(path)
        await store.init()  # must ALTER-add plan_version + floor_soc, preserving the legacy row
        await store.record_plan("2026-06-28T10:15:00+00:00", {
            "strategy": "winter", "target_soc": 80.0, "deadline": "2026-06-28T18:00:00+00:00",
            "soc_pct": 58.0, "intent": "discharge_for_load",
            "plan_version": "epoch-1", "floor_soc": 10.0})
        return await store.plan_history_between(
            "2026-06-28T00:00:00+00:00", "2026-06-29T00:00:00+00:00")

    rows = asyncio.run(run())
    assert len(rows) == 2
    assert rows[0]["target_soc"] == 80.0  # legacy row preserved
    assert rows[0]["plan_version"] is None and rows[0]["floor_soc"] is None
    assert rows[1]["plan_version"] == "epoch-1" and rows[1]["floor_soc"] == 10.0


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


def test_car_soc_anchor_set_get_roundtrip(tmp_path):
    # feat/ev-charging: the manual (pct, ts) car-SoC anchor persists in a KV row.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.set_car_soc_anchor(42.5, "2026-07-12T08:00:00+00:00")
        return await store.get_car_soc_anchor()

    assert asyncio.run(run()) == (42.5, "2026-07-12T08:00:00+00:00")


def test_car_soc_anchor_overwrites_previous(tmp_path):
    # Re-anchoring after a drive replaces the old anchor — there is only one 'last known' value.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.set_car_soc_anchor(40.0, "2026-07-12T08:00:00+00:00")
        await store.set_car_soc_anchor(63.0, "2026-07-12T20:00:00+00:00")
        return await store.get_car_soc_anchor()

    assert asyncio.run(run()) == (63.0, "2026-07-12T20:00:00+00:00")


def test_car_soc_anchor_none_when_unset(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        return await store.get_car_soc_anchor()

    assert asyncio.run(run()) is None


def test_add_notification_stores_row_with_in_app_delivered_default(tmp_path):
    # B-20: the row itself IS the in-app delivery — `delivered` defaults to ["in_app"].
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        nid = await store.add_notification(
            "2026-07-13T10:00:00+00:00", "backup_failed", "Backup failed", "It didn't complete.",
        )
        rows = await store.notifications_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
        return nid, rows

    nid, rows = asyncio.run(run())
    assert nid is not None
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == nid
    assert row["key"] == "backup_failed"
    assert row["title"] == "Backup failed"
    assert row["body"] == "It didn't complete."
    assert row["confidence"] is None
    assert row["read"] is False
    assert row["delivered"] == ["in_app"]
    assert row["dedupe_key"] is None


def test_add_notification_dedupes_same_key_but_new_key_gets_through(tmp_path):
    # A repeat with the SAME dedupe_key is suppressed (returns None, no new row); a DIFFERENT key
    # (e.g. the caller bakes in a new local day) always gets through.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        first = await store.add_notification(
            "2026-07-13T09:00:00+00:00", "backup_failed", "Backup failed", "day 1 attempt 1",
            dedupe_key="backup_failed:2026-07-13",
        )
        second = await store.add_notification(
            "2026-07-13T15:00:00+00:00", "backup_failed", "Backup failed", "day 1 attempt 2",
            dedupe_key="backup_failed:2026-07-13",
        )
        third = await store.add_notification(
            "2026-07-14T09:00:00+00:00", "backup_failed", "Backup failed", "day 2 attempt 1",
            dedupe_key="backup_failed:2026-07-14",
        )
        rows = await store.notifications_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
        return first, second, third, rows

    first, second, third, rows = asyncio.run(run())
    assert first is not None
    assert second is None  # deduped — same key, same day
    assert third is not None  # a new day's key always gets through
    assert [r["body"] for r in rows] == ["day 1 attempt 1", "day 2 attempt 1"]


def test_set_notification_delivered_overwrites_channel_list(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        nid = await store.add_notification(
            "2026-07-13T10:00:00+00:00", "backup_failed", "Backup failed", "body")
        await store.set_notification_delivered(nid, ["in_app", "ntfy"])
        rows = await store.notifications_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
        return rows

    rows = asyncio.run(run())
    assert rows[0]["delivered"] == ["in_app", "ntfy"]


def test_notifications_between_is_windowed_and_oldest_first(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.add_notification("2026-07-10T10:00:00+00:00", "k", "t", "before window")
        await store.add_notification("2026-07-13T10:00:00+00:00", "k", "t", "first in window")
        await store.add_notification("2026-07-14T10:00:00+00:00", "k", "t", "second in window")
        return await store.notifications_between(
            "2026-07-12T00:00:00+00:00", "2026-07-15T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [r["body"] for r in rows] == ["first in window", "second in window"]


def test_unread_count_and_mark_notifications_read_by_ids(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        a = await store.add_notification("2026-07-13T10:00:00+00:00", "k", "t", "a")
        await store.add_notification("2026-07-13T11:00:00+00:00", "k", "t", "b")
        before = await store.unread_count()
        changed = await store.mark_notifications_read(ids=[a])
        after = await store.unread_count()
        return before, changed, after

    before, changed, after = asyncio.run(run())
    assert before == 2
    assert changed == 1
    assert after == 1


def test_mark_notifications_read_all(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.add_notification("2026-07-13T10:00:00+00:00", "k", "t", "a")
        await store.add_notification("2026-07-13T11:00:00+00:00", "k", "t", "b")
        changed = await store.mark_notifications_read(mark_all=True)
        return changed, await store.unread_count()

    changed, unread = asyncio.run(run())
    assert changed == 2
    assert unread == 0


def test_mark_notifications_read_noop_without_ids_or_all(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.add_notification("2026-07-13T10:00:00+00:00", "k", "t", "a")
        changed = await store.mark_notifications_read()
        return changed, await store.unread_count()

    changed, unread = asyncio.run(run())
    assert changed == 0
    assert unread == 1


def test_purge_trims_notifications_by_ts_but_keeps_recent(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.add_notification("2026-01-01T10:00:00+00:00", "k", "t", "old")  # purged
        await store.add_notification("2026-06-28T10:00:00+00:00", "k", "t", "kept")
        deleted = await store.purge_older_than("2026-06-01T00:00:00+00:00")
        rows = await store.notifications_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
        return deleted, rows

    deleted, rows = asyncio.run(run())
    assert deleted >= 1
    assert [r["body"] for r in rows] == ["kept"]
