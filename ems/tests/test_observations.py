"""Compact 15-min observation store (design §4.1) + daily kWh rollups (B-13): materializer math,
EV exclusion, cadence-aware coverage + the low_coverage flag boundary, a DST-day slot count,
backfill-on-migration, daily maintenance idempotence, and the 400-day purge leaving daily_energy."""
import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.storage.history import (
    HistoryStore,
    aggregate_observations,
    materialize_daily_energy,
    materialize_observations,
)

_AMS = ZoneInfo("Europe/Amsterdam")


def _raw(grid=0.0, solar=0.0, battery=0.0, ev=0.0, soc=50.0):
    return RawSample(grid_power_w=grid, solar_power_w=solar, battery_power_w=battery,
                     ev_power_w=ev, soc_pct=soc)


async def _seed(store, samples):
    """samples: list of (ts_iso, RawSample). Records raw+derived (derived via reconstruct)."""
    for ts, raw in samples:
        await store.record(ts, raw, reconstruct(raw))


def test_materialize_observation_means_and_ev_exclusion(tmp_path):
    # One full 10:00 UTC slot, 3 samples @300s cadence ⇒ coverage 1.0, no flag. mean_load_w
    # includes EV (the :10 sample charges the car at 900 W); mean_non_ev_load_w excludes it — the
    # load_model threshold rule (ev > 200 W) applied at ingest, so the two means diverge by the EV.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await _seed(store, [
            ("2026-07-10T10:00:00+00:00", _raw(grid=300, solar=600)),   # load 900, non_ev 900
            ("2026-07-10T10:05:00+00:00", _raw(grid=300)),              # load 300, non_ev 300
            ("2026-07-10T10:10:00+00:00", _raw(grid=1200, ev=900)),     # load 1200, non_ev 300
        ])
        await materialize_observations(
            store, datetime(2026, 7, 10, tzinfo=UTC), datetime(2026, 7, 11, tzinfo=UTC))
        return await store.observations_between("2026-07-10T00:00:00+00:00",
                                                "2026-07-11T00:00:00+00:00")

    rows = asyncio.run(run())
    assert len(rows) == 1
    o = rows[0]
    assert o["slot_start"] == "2026-07-10T10:00:00+00:00"
    assert o["mean_load_w"] == 800.0        # (900+300+1200)/3
    assert o["mean_non_ev_load_w"] == 500.0  # (900+300+300)/3 — EV (900 W) excluded from the :10
    assert o["mean_solar_w"] == 200.0        # (600+0+0)/3
    assert o["samples"] == 3
    assert o["coverage"] == 1.0
    assert o["flags"] == []


def test_low_coverage_flag_boundary_is_strict(tmp_path):
    # cadence 60s ⇒ 15 expected samples/slot. Exactly 0.8 (12/15) is NOT flagged (strict <); 11/15
    # is. Two adjacent slots so one materialize pass exercises both sides of the boundary.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        # slot 10:00 — 12 samples at :00..:11 ⇒ coverage 0.8 exactly
        await _seed(store, [
            (f"2026-07-10T10:{m:02d}:00+00:00", _raw(grid=100)) for m in range(12)])
        # slot 10:15 — 11 samples at :15..:25 ⇒ coverage 11/15 ≈ 0.733
        await _seed(store, [
            (f"2026-07-10T10:{m:02d}:00+00:00", _raw(grid=100)) for m in range(15, 26)])
        await materialize_observations(
            store, datetime(2026, 7, 10, tzinfo=UTC), datetime(2026, 7, 11, tzinfo=UTC),
            cadence_seconds=60.0)
        return await store.observations_between("2026-07-10T00:00:00+00:00",
                                                "2026-07-11T00:00:00+00:00")

    rows = asyncio.run(run())
    by = {r["slot_start"]: r for r in rows}
    at_boundary = by["2026-07-10T10:00:00+00:00"]
    assert at_boundary["samples"] == 12
    assert round(at_boundary["coverage"], 4) == 0.8
    assert at_boundary["flags"] == []  # 0.8 is NOT < 0.8
    below = by["2026-07-10T10:15:00+00:00"]
    assert below["samples"] == 11
    assert round(below["coverage"], 4) == round(11 / 15, 4)
    assert below["flags"] == ["low_coverage"]


def test_aggregate_observations_is_pure_and_coverage_caps_at_one():
    # Pure helper: more samples than expected (over-sampling) caps coverage at 1.0, never > 1.
    raw = [{"ts": f"2026-07-10T10:0{m}:00+00:00", "solar_power_w": 100.0} for m in range(5)]
    der = [{"ts": f"2026-07-10T10:0{m}:00+00:00", "house_load_w": 500.0,
            "non_ev_load_w": 500.0} for m in range(5)]
    out = aggregate_observations(raw, der, cadence_seconds=300.0)  # expected 3, have 5
    assert len(out) == 1
    assert out[0]["samples"] == 5
    assert out[0]["coverage"] == 1.0
    assert out[0]["flags"] == []


def test_daily_energy_kwh_and_sign_conventions(tmp_path):
    # A local day with one 12:00-local slot: solar 1000 W, grid −800 W (export), battery −400 W
    # (charging), load 600 W. Integrated over the 3 samples' single 15-min slot (0.25 h).
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        # 12:00 Amsterdam (summer) = 10:00 UTC. grid −800 export, battery −400 charge.
        # house_load = grid + solar + battery = -800 + 1000 - 400 = -200 ... avoid negative:
        # pick grid so load is sensible: grid=+? We want load 600 ⇒ grid = 600 - solar - battery.
        # Keep export sign test separate below; here use grid=-800 and accept the arithmetic load.
        for m in (0, 5, 10):
            raw = _raw(grid=-800, solar=1000, battery=-400)  # load = -200 W
            await store.record(f"2026-07-10T10:{m:02d}:00+00:00", raw, reconstruct(raw))
        day_start = datetime(2026, 7, 10, tzinfo=_AMS)
        day_end = day_start + timedelta(days=1)
        await materialize_observations(store, day_start, day_end)
        await materialize_daily_energy(store, day_start, day_end)
        return await store.daily_energy_between("2026-07-10", "2026-07-11")

    rows = asyncio.run(run())
    assert len(rows) == 1
    d = rows[0]
    assert d["date"] == "2026-07-10"
    assert d["solar_kwh"] == 0.25          # 1000 W × 0.25 h / 1000
    assert d["grid_export_kwh"] == 0.2     # 800 W export × 0.25 h / 1000
    assert d["grid_import_kwh"] == 0.0     # net was export ⇒ no import
    assert d["battery_charge_kwh"] == 0.1  # 400 W into battery × 0.25 h / 1000
    assert d["battery_discharge_kwh"] == 0.0


def test_daily_energy_ev_split(tmp_path):
    # ev_kwh is load_kwh − non_ev_load_kwh: EV charging above the threshold is carved out.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        for m in (0, 5, 10):
            raw = _raw(grid=1200, ev=900)  # load 1200, non_ev 300 (ev excluded), ev 900
            await store.record(f"2026-07-10T10:{m:02d}:00+00:00", raw, reconstruct(raw))
        day_start = datetime(2026, 7, 10, tzinfo=_AMS)
        day_end = day_start + timedelta(days=1)
        await materialize_observations(store, day_start, day_end)
        await materialize_daily_energy(store, day_start, day_end)
        return await store.daily_energy_between("2026-07-10", "2026-07-11")

    d = asyncio.run(run())[0]
    assert d["load_kwh"] == 0.3          # 1200 W × 0.25 h / 1000
    assert d["non_ev_load_kwh"] == 0.075  # 300 W × 0.25 h / 1000
    assert d["ev_kwh"] == 0.225           # 900 W × 0.25 h / 1000


def test_daily_energy_coverage_is_dst_aware(tmp_path):
    # 2026-10-25 is the EU fall-back day in Amsterdam: a 25-HOUR local day. The coverage
    # denominator (expected samples) must use that real 25 h span, not a fixed 24 h.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    day_start = datetime(2026, 10, 25, tzinfo=_AMS)
    day_end = day_start + timedelta(days=1)
    # Sanity via UTC (same-tzinfo subtraction would naively read 24 h): it really is a 25 h day.
    assert (day_end.astimezone(UTC) - day_start.astimezone(UTC)) == timedelta(hours=25)

    async def run():
        await store.init()
        # 6 samples inside the day (00:00 & 00:05 UTC = 02:00 & 02:05 local, comfortably inside).
        for m in (0, 5, 10, 15, 20, 25):
            raw = _raw(grid=100)
            await store.record(f"2026-10-25T00:{m:02d}:00+00:00", raw, reconstruct(raw))
        await materialize_observations(store, day_start, day_end, cadence_seconds=300.0)
        await materialize_daily_energy(store, day_start, day_end, cadence_seconds=300.0)
        return await store.daily_energy_between("2026-10-25", "2026-10-26")

    d = asyncio.run(run())[0]
    expected_samples = 25 * 3600 / 300  # 300 (a 24 h assumption would give 288 ⇒ 0.0208)
    assert d["coverage"] == round(6 / expected_samples, 4) == 0.02


def test_backfill_on_migration_populates_observations_and_daily_energy(tmp_path):
    # Migrating an EXISTING v0 DB materializes observations AND daily_energy from ALL existing raw
    # history (production's weeks of samples count from day one). Verified end-to-end via init().
    path = str(tmp_path / "ems.sqlite")

    async def build_v0():
        async with aiosqlite.connect(path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "CREATE TABLE raw_samples "
                "(ts TEXT NOT NULL, grid_power_w REAL NOT NULL, solar_power_w REAL NOT NULL, "
                "battery_power_w REAL NOT NULL, ev_power_w REAL NOT NULL, soc_pct REAL NOT NULL)")
            await db.execute(
                "CREATE TABLE derived_samples "
                "(ts TEXT NOT NULL, house_load_w REAL NOT NULL, non_ev_load_w REAL NOT NULL)")
            for m in (0, 5, 10):
                raw = _raw(grid=-500, solar=1000)  # export 500, load 500
                der = reconstruct(raw)
                await db.execute("INSERT INTO raw_samples VALUES (?,?,?,?,?,?)",
                                 (f"2026-07-10T10:{m:02d}:00+00:00", raw.grid_power_w,
                                  raw.solar_power_w, raw.battery_power_w, raw.ev_power_w, 50.0))
                await db.execute("INSERT INTO derived_samples VALUES (?,?,?)",
                                 (f"2026-07-10T10:{m:02d}:00+00:00", der.house_load_w,
                                  der.non_ev_load_w))
            await db.commit()

    async def run():
        await build_v0()
        store = HistoryStore(path)
        await store.init()  # runs migrations v1 (observations) + v2 (daily_energy), both backfilled
        obs = await store.observations_between("0000", "9999")
        daily = await store.daily_energy_between("0000", "9999")
        return await store.schema_version(), obs, daily

    version, obs, daily = asyncio.run(run())
    assert version == 2
    assert len(obs) == 1 and obs[0]["mean_solar_w"] == 1000.0
    assert len(daily) == 1  # backfill keys by UTC day (storage layer has no site tz at migration)
    d = daily[0]
    assert d["date"] == "2026-07-10"
    assert d["solar_kwh"] == 0.25
    assert d["grid_export_kwh"] == 0.125
    assert d["grid_import_kwh"] == 0.0


def test_daily_maintenance_materialize_is_idempotent(tmp_path):
    # Re-materializing the same day (a restart / retried maintenance cycle) is an upsert: the row
    # count and values are stable, never duplicated.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        for m in (0, 5, 10):
            raw = _raw(grid=300, solar=200)
            await store.record(f"2026-07-10T10:{m:02d}:00+00:00", raw, reconstruct(raw))
        day_start = datetime(2026, 7, 10, tzinfo=_AMS)
        day_end = day_start + timedelta(days=1)
        await materialize_observations(store, day_start, day_end)
        await materialize_daily_energy(store, day_start, day_end)
        first_obs = await store.observations_between("0000", "9999")
        first_daily = await store.daily_energy_between("0000", "9999")
        # Run again — must not duplicate or change anything.
        await materialize_observations(store, day_start, day_end)
        await materialize_daily_energy(store, day_start, day_end)
        second_obs = await store.observations_between("0000", "9999")
        second_daily = await store.daily_energy_between("0000", "9999")
        return first_obs, second_obs, first_daily, second_daily

    fo, so, fd, sd = asyncio.run(run())
    assert fo == so and len(so) == 1
    assert fd == sd and len(sd) == 1


def test_observation_purge_leaves_daily_energy_alone(tmp_path):
    # Observations purge at their own 400-day horizon; daily_energy is NEVER purged (the whole
    # point — year-over-year kWh survives). Raw purge_older_than must also not touch either.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        # An OLD day (well past 400 days) and a RECENT day.
        for day in ("2024-01-01", "2026-07-10"):
            for m in (0, 5, 10):
                raw = _raw(grid=300, solar=100)
                await store.record(f"{day}T10:{m:02d}:00+00:00", raw, reconstruct(raw))
            ds = datetime.fromisoformat(f"{day}T00:00:00+00:00")
            await materialize_observations(store, ds, ds + timedelta(days=1))
            await materialize_daily_energy(store, ds, ds + timedelta(days=1))
        cutoff = (datetime(2026, 7, 14, tzinfo=UTC) - timedelta(days=400)).isoformat()
        # Raw retention purge must NOT touch observations or daily_energy.
        await store.purge_older_than("2025-01-01T00:00:00+00:00")
        obs_before = await store.observations_between("0000", "9999")
        purged = await store.purge_observations_older_than(cutoff)
        obs_after = await store.observations_between("0000", "9999")
        daily = await store.daily_energy_between("0000", "9999")
        return len(obs_before), purged, obs_after, daily

    before, purged, obs_after, daily = asyncio.run(run())
    assert before == 2  # both days materialized before purge (raw purge left observations intact)
    assert purged == 1  # only the 2024 slot fell outside the 400-day observation horizon
    assert [o["slot_start"] for o in obs_after] == ["2026-07-10T10:00:00+00:00"]
    assert {d["date"] for d in daily} == {"2024-01-01", "2026-07-10"}  # daily_energy untouched
