"""F-48 / #342: catch-up of the last 7 rollup days, provisional flags, missed-rollup alert.

Service tests inject FrozenClock. No battery writes.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ems.clock import FrozenClock
from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.rollups import (
    completed_lookback_days,
    day_is_provisional,
    missing_rollup_days,
    past_rollup_deadline,
)
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import _FINANCE_CALC_VERSION, create_app

AMS = ZoneInfo("Europe/Amsterdam")


def test_lookback_is_seven_days_and_deadline_is_noon():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=AMS)
    days = completed_lookback_days(now)
    assert days == [date(2026, 6, 22) + timedelta(days=i) for i in range(7)]
    assert date(2026, 6, 29) not in days
    assert date(2026, 6, 21) not in days
    assert past_rollup_deadline(now, date(2026, 6, 28)) is True
    earlier = now.replace(hour=11, minute=59)
    assert past_rollup_deadline(earlier, date(2026, 6, 28)) is False
    at_half_past = datetime(2026, 6, 29, 0, 30, tzinfo=AMS)
    assert past_rollup_deadline(at_half_past, date(2026, 6, 28)) is False
    assert day_is_provisional(has_rollup=False, sample_coverage=1.0) is True
    assert day_is_provisional(has_rollup=True, sample_coverage=None) is True
    assert day_is_provisional(has_rollup=True, sample_coverage=0.949) is True
    assert day_is_provisional(has_rollup=True, sample_coverage=0.95) is False


async def _record(store: HistoryStore, day: date) -> None:
    ts = datetime(day.year, day.month, day.day, 12, tzinfo=AMS).astimezone(UTC)
    raw = RawSample(
        grid_power_w=500.0, solar_power_w=0.0, battery_power_w=0.0,
        ev_power_w=0.0, soc_pct=50.0,
    )
    iso = ts.isoformat()
    await store.record(iso, raw, reconstruct(raw))
    await store.upsert_price_slots([(iso, 0.25)])


def _app(db: str, when: datetime, store: HistoryStore | None = None):
    from ems.tests.conftest import NO_HISTORY_PURGE

    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=store or HistoryStore(db), settings_store=SettingsStore(db),
        price_source=MockPriceSource(AMS), clock=FrozenClock(when),
        **NO_HISTORY_PURGE,
    )


async def _energy(db: str) -> list[dict]:
    store = HistoryStore(db)
    rows = await store.daily_energy_between("0000", "9999")
    await store.close()
    return rows


async def _finance(db: str) -> list[dict]:
    store = HistoryStore(db)
    rows = await store.daily_finance_between("0000", "9999")
    await store.close()
    return rows


@pytest.mark.parametrize(
    ("label", "when", "seed", "expect", "older"),
    [
        (
            "normal",
            datetime(2026, 6, 29, 6, 0, tzinfo=AMS),
            [date(2026, 6, 28)],
            [date(2026, 6, 28)],
            date(2026, 6, 21),
        ),
        (
            "restart-0030",
            datetime(2026, 6, 29, 0, 30, tzinfo=AMS),
            [date(2026, 6, 28)],
            [date(2026, 6, 28)],
            date(2026, 6, 21),
        ),
        (
            "two-days-down",
            datetime(2026, 6, 30, 10, 0, tzinfo=AMS),
            [date(2026, 6, 28), date(2026, 6, 29)],
            [date(2026, 6, 28), date(2026, 6, 29)],
            date(2026, 6, 22),
        ),
    ],
)
def test_catchup_writes_rollup_before_noon_or_marks_provisional(
    tmp_path, label, when, seed, expect, older,
):
    """AC1: day D has a rollup before 12:00 D+1, or the day is provisional."""
    db = str(tmp_path / "ems.sqlite")
    store = HistoryStore(db)

    async def seed_db() -> None:
        await store.init()
        for day in [*seed, older]:
            await _record(store, day)
        await store.close()

    asyncio.run(seed_db())
    with TestClient(_app(db, when, store)):
        pass

    energy = {row["date"]: row for row in asyncio.run(_energy(db))}
    finance = {row["day"]: row for row in asyncio.run(_finance(db))}
    for day in expect:
        assert day.isoformat() in energy, label
        assert day.isoformat() in finance, label
        assert finance[day.isoformat()]["data"].get("provisional") is None
    assert older.isoformat() not in energy
    assert older.isoformat() not in finance

    # Second boot is idempotent: still one row per day, same energy figures.
    first_energy = dict(energy)
    with TestClient(_app(db, when, store)):
        pass
    again = {row["date"]: row for row in asyncio.run(_energy(db))}
    for day in expect:
        assert again[day.isoformat()] == first_energy[day.isoformat()]
    assert len(again) == len(first_energy)

    with TestClient(_app(db, when, store)) as client:
        for day in expect:
            body = client.get(f"/api/finance?period=day&date={day.isoformat()}").json()
            assert body["days"][0]["provisional"] is True  # one sample is under 95%
            energy_body = client.get(
                f"/api/energy-distribution?date={day.isoformat()}",
            ).json()
            assert energy_body["provisional"] is True
        alerts = {a["key"] for a in client.get("/api/alerts").json()["alerts"]}
        assert "rollup_missing" not in alerts


def test_cached_rollup_is_not_overwritten_with_empty_data(tmp_path):
    """AC4: #131 stays — no samples and an existing row are left byte-for-byte."""
    db = str(tmp_path / "ems.sqlite")
    sentinel = {
        "day": "2026-06-28", "has_data": True, "saved_eur": 12.34,
        "price_coverage": 1.0, "sample_coverage": 1.0, "calc_v": _FINANCE_CALC_VERSION,
    }
    store = HistoryStore(db)

    async def seed_db() -> None:
        await store.init()
        await store.upsert_daily_finance("2026-06-28", dict(sentinel))
        await store.close()

    asyncio.run(seed_db())
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO daily_energy (date, solar_kwh, load_kwh, non_ev_load_kwh, ev_kwh, "
            "grid_import_kwh, grid_export_kwh, battery_charge_kwh, battery_discharge_kwh, "
            "coverage) VALUES ('2026-06-28', 5, 1, 1, 0, 1, 0, 0, 0, 1.0)",
        )
        conn.commit()

    when = datetime(2026, 6, 29, 6, 0, tzinfo=AMS)
    with TestClient(_app(db, when, store)):
        pass
    with TestClient(_app(db, when, store)):
        pass

    finance = asyncio.run(_finance(db))
    assert len(finance) == 1
    assert finance[0]["data"] == sentinel
    energy = asyncio.run(_energy(db))
    assert len(energy) == 1
    assert energy[0]["solar_kwh"] == 5
    assert energy[0]["coverage"] == 1.0


def test_provisional_follows_coverage_on_finance_and_energy(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    store = HistoryStore(db)

    async def seed_db() -> None:
        await store.init()
        for day, coverage in (("2026-06-27", 0.94), ("2026-06-28", 0.96)):
            await store.upsert_daily_finance(day, {
                "day": day, "has_data": True, "saved_eur": 1.0,
                "price_coverage": 1.0, "sample_coverage": coverage,
                "calc_v": _FINANCE_CALC_VERSION,
            })
        await store.close()

    asyncio.run(seed_db())
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO daily_energy (date, solar_kwh, load_kwh, non_ev_load_kwh, ev_kwh, "
            "grid_import_kwh, grid_export_kwh, battery_charge_kwh, battery_discharge_kwh, "
            "coverage) VALUES ('2026-06-28', 1, 1, 1, 0, 1, 0, 0, 0, 0.96)",
        )
        conn.execute(
            "INSERT INTO daily_energy (date, solar_kwh, load_kwh, non_ev_load_kwh, ev_kwh, "
            "grid_import_kwh, grid_export_kwh, battery_charge_kwh, battery_discharge_kwh, "
            "coverage) VALUES ('2026-06-27', 1, 1, 1, 0, 1, 0, 0, 0, 0.90)",
        )
        conn.commit()

    when = datetime(2026, 6, 29, 8, 0, tzinfo=AMS)
    with TestClient(_app(db, when, store)) as client:
        full = client.get("/api/finance?period=day&date=2026-06-28").json()["days"][0]
        sparse = client.get("/api/finance?period=day&date=2026-06-27").json()["days"][0]
        assert full["provisional"] is False
        assert sparse["provisional"] is True
        assert client.get("/api/energy-distribution?date=2026-06-28").json()["provisional"] is False
        assert client.get("/api/energy-distribution?date=2026-06-27").json()["provisional"] is True
        assert client.get("/api/energy-distribution?date=2026-06-26").json()["provisional"] is True
        week = client.get("/api/report?period=week&date=2026-06-28").json()
        by_day = {point["start"][:10]: point["provisional"] for point in week["series"]}
        assert by_day["2026-06-28"] is False
        assert by_day["2026-06-27"] is True
        day = client.get("/api/report?period=day&date=2026-06-26").json()
        assert day["provisional"] is True


def test_rollup_missing_alert_clears_on_the_api_when_the_rollup_exists(tmp_path, monkeypatch):
    """Sampled day past noon, job skipped: alert + diagnostics counter, then both clear."""

    async def _noop(*_args, **_kwargs):
        return []

    monkeypatch.setattr("ems.web.api.catch_up_rollups", _noop)
    db = str(tmp_path / "ems.sqlite")
    store = HistoryStore(db)

    async def seed_db() -> None:
        await store.init()
        await _record(store, date(2026, 6, 28))
        await store.close()

    asyncio.run(seed_db())
    when = datetime(2026, 6, 29, 12, 0, tzinfo=AMS)
    with TestClient(_app(db, when, store)) as client:
        alerts = {a["key"] for a in client.get("/api/alerts").json()["alerts"]}
        assert "rollup_missing" in alerts
        diag = client.get("/api/diagnostics").json()
        assert diag["rollup_missing_count"] == 1
        rollup = next(c for c in diag["checks"] if c["key"] == "rollup")
        assert rollup["status"] == "warn"
        assert rollup["detail"].startswith("1 ")

        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO daily_energy (date, solar_kwh, load_kwh, non_ev_load_kwh, ev_kwh, "
                "grid_import_kwh, grid_export_kwh, battery_charge_kwh, battery_discharge_kwh, "
                "coverage) VALUES ('2026-06-28', 1, 1, 1, 0, 1, 0, 0, 0, 1.0)",
            )
            conn.execute(
                "INSERT INTO daily_finance (day, data) VALUES (?, ?)",
                ("2026-06-28", json.dumps({"calc_v": _FINANCE_CALC_VERSION, "has_data": True})),
            )
            conn.commit()
        alerts = {a["key"] for a in client.get("/api/alerts").json()["alerts"]}
        assert "rollup_missing" not in alerts
        assert client.get("/api/diagnostics").json()["rollup_missing_count"] == 0
        assert missing_rollup_days(db, when) == []
