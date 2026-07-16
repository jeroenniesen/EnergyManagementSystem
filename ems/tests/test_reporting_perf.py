"""Micro-benchmarks / query-count evidence for BACKLOG B-49 (reporting query performance).

These don't assert wall-clock timing (flaky across machines/CI) — they assert QUERY COUNT via a
store-method-level counter, which is what actually changed: /api/finance moving from an O(days)
round-trip loop to O(1), and /api/report's year series moving from re-iterating raw/derived rows
to reading the daily_energy rollup. Seeds ~90 days of raw history at a 300s cadence (via direct
executemany — bypassing HistoryStore.record()'s one-row-at-a-time API, which would itself dominate
the test's runtime and isn't the thing under test) in a year (2025) that's fully in the past
regardless of when this test runs, so window resolution and the "completed day" cache-guard logic
are both deterministic.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore, materialize_daily_energy
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")
YEAR = 2025          # fully in the past — deterministic "completed" windows regardless of `now`
SEED_DAYS = 90        # BACKLOG B-49's own scale ("~90 days")
CADENCE_S = 300       # BACKLOG B-49's own cadence ("300s cadence")


async def _seed(db: str) -> None:
    store = HistoryStore(db)
    await store.init()
    t0 = datetime(YEAR, 1, 1, tzinfo=UTC)
    per_day = int(86400 / CADENCE_S)
    raw_rows: list[tuple] = []
    der_rows: list[tuple] = []
    price_rows: list[tuple] = []
    for d in range(SEED_DAYS):
        day0 = t0 + timedelta(days=d)
        for i in range(per_day):
            ts = (day0 + timedelta(seconds=CADENCE_S * i)).isoformat()
            raw_rows.append((ts, 300.0, 500.0, 0.0, 0.0, 50.0))
            der_rows.append((ts, 1000.0, 1000.0))
        # One price per hour is plenty for a coverage/cache-guard exercise — the assertions are
        # about CALL COUNT, not exact euro figures.
        for h in range(24):
            price_rows.append(((day0 + timedelta(hours=h)).isoformat(), 0.20))
    # Bulk seed via a SEPARATE short-lived connection (fast executemany) — HistoryStore's own
    # persistent connection (opened by store.init() above) sees the committed rows immediately
    # (same file, WAL mode).
    async with aiosqlite.connect(db) as con:
        await con.executemany(
            "INSERT INTO raw_samples "
            "(ts, grid_power_w, solar_power_w, battery_power_w, ev_power_w, soc_pct) "
            "VALUES (?, ?, ?, ?, ?, ?)", raw_rows)
        await con.executemany(
            "INSERT INTO derived_samples (ts, house_load_w, non_ev_load_w) VALUES (?, ?, ?)",
            der_rows)
        await con.executemany(
            "INSERT INTO price_slots (start_ts, eur_per_kwh) VALUES (?, ?)", price_rows)
        await con.commit()
    # daily_energy rollups (B-13) for the same seeded days, so the year-report test has real rows
    # to pre-aggregate from.
    for d in range(SEED_DAYS):
        day_start = datetime(YEAR, 1, 1, tzinfo=AMS) + timedelta(days=d)
        await materialize_daily_energy(store, day_start, day_start + timedelta(days=1),
                                       cadence_seconds=CADENCE_S)


def _app(db: str):
    # history_retention_days=0: the seeded year (2025) is deliberately far in the past for
    # deterministic "completed window" resolution — the maintenance loop's "purge on boot" must
    # not sweep it away before the test ever queries it (retention purging is unrelated to what's
    # under test here).
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db), settings_store=SettingsStore(db),
        price_source=MockPriceSource(AMS), history_retention_days=0,
    )


def _count_method(
    monkeypatch, cls, name: str, calls: dict, *, arg_before: str | None = None
) -> None:
    orig = getattr(cls, name)
    calls[name] = 0

    async def counting(self, *a, **kw):
        # `arg_before` scopes the count to calls whose first (window-start) arg is strictly before
        # it. The measured YEAR-2025 window's starts are all < "2026-01-01" (even the UTC-ISO ones,
        # which fall on 2024-12-31 at the AMS offset), while the boot-time maintenance loop's
        # YESTERDAY-finance caching (F5) queries the current year (>= 2026) — so it is
        # DETERMINISTICALLY excluded regardless of task scheduling.
        if arg_before is None or (a and isinstance(a[0], str) and a[0] < arg_before):
            calls[name] += 1
        return await orig(self, *a, **kw)

    monkeypatch.setattr(cls, name, counting)


def test_finance_year_view_is_o1_db_round_trips_not_o_days(tmp_path, monkeypatch):
    # Before BACKLOG B-49: /api/finance?period=year looped ONE `_ensure_day_finance` per day (up
    # to 365 for a full year), each doing 1-3 store round trips (>=90-365+ total for 90+ seeded
    # days). After: the whole window's raw/prices/cached-finance are each fetched ONCE.
    db = str(tmp_path / "ems.sqlite")
    asyncio.run(_seed(db))

    calls: dict = {}
    for name in ("raw_between", "prices_between", "daily_finance_between"):
        # Scope to the measured 2025 window (excludes the boot maintenance's yesterday-finance, F5).
        _count_method(monkeypatch, HistoryStore, name, calls, arg_before=f"{YEAR + 1}-01-01")

    with TestClient(_app(db)) as c:
        body = c.get(f"/api/finance?period=year&date={YEAR}-06-15").json()

    # Sanity: the seeded days were found (>= rather than == — seeding UTC-aligned days can spill
    # one extra hour into a 91st LOCAL AMS calendar day at the tz-offset boundary).
    assert body["totals"]["days_with_data"] >= SEED_DAYS
    total_calls = sum(calls.values())
    assert total_calls <= 4, calls  # O(1), not O(days) — was >= SEED_DAYS (90) before batching
    assert calls["raw_between"] == 1
    assert calls["prices_between"] == 1
    assert calls["daily_finance_between"] == 1


def test_finance_day_view_still_works_unbatched_path_untouched(tmp_path, monkeypatch):
    # Sanity: the single-day path (still also used by the export package via `_ensure_day_finance`,
    # untouched) keeps working — the batching only changes /api/finance's OWN fetch strategy.
    db = str(tmp_path / "ems.sqlite")
    asyncio.run(_seed(db))
    with TestClient(_app(db)) as c:
        body = c.get(f"/api/finance?period=day&date={YEAR}-01-05").json()
    assert len(body["days"]) == 1
    assert body["days"][0]["has_data"] is True


def test_report_year_series_reads_rollup_raw_reads_stay_single(tmp_path, monkeypatch):
    # BACKLOG B-49 §4: for period=year, series comes from daily_energy (the rollup) — this does
    # NOT eliminate raw_between/derived_between (build_report's Sankey flows still need them), but
    # it must not ALSO fetch raw a second time for the series (the naive mistake this guards
    # against), and daily_energy_between must be touched exactly once.
    db = str(tmp_path / "ems.sqlite")
    asyncio.run(_seed(db))

    calls: dict = {}
    for name in ("raw_between", "derived_between", "daily_energy_between"):
        # Scope to the measured 2025 window (excludes the boot maintenance's yesterday-finance, F5).
        _count_method(monkeypatch, HistoryStore, name, calls, arg_before=f"{YEAR + 1}-01-01")

    with TestClient(_app(db)) as c:
        body = c.get(f"/api/report?period=year&date={YEAR}-06-15").json()

    assert len(body["series"]) == 12
    assert calls["daily_energy_between"] == 1
    assert calls["raw_between"] == 1        # NOT re-fetched for series
    assert calls["derived_between"] == 1


def test_report_day_view_never_touches_daily_energy(tmp_path, monkeypatch):
    db = str(tmp_path / "ems.sqlite")
    asyncio.run(_seed(db))
    calls: dict = {}
    _count_method(monkeypatch, HistoryStore, "daily_energy_between", calls)
    with TestClient(_app(db)) as c:
        c.get(f"/api/report?period=day&date={YEAR}-01-05").json()
    assert calls["daily_energy_between"] == 0
