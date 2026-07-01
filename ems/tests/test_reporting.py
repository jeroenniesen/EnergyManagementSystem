"""Insights report assembly: window resolution (day/week/month/year), pure build_report, and the
/api/report endpoint over a seeded history DB. No hardware."""
import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.load_model import DerivedSample
from ems.reporting import build_report, resolve_window
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")
PAST = datetime(2026, 6, 28, 12, 0, tzinfo=AMS)  # a completed past day (deterministic)


class _P:
    """Minimal price slot (build_report only reads .start / .eur_per_kwh)."""

    def __init__(self, start: datetime, eur: float):
        self.start = start
        self.eur_per_kwh = eur


def test_resolve_window_all_periods():
    tz = AMS
    now = datetime(2026, 7, 1, 12, 0, tzinfo=tz)  # a Wednesday
    anchor = date(2026, 7, 1)
    s, e, label, partial = resolve_window("day", anchor, tz, now)
    assert label == "2026-07-01" and (e - s) == timedelta(days=1) and partial is True
    s, e, _, _ = resolve_window("week", anchor, tz, now)
    assert (e - s) == timedelta(days=7) and s.date() == date(2026, 6, 29)  # Monday
    s, e, label, _ = resolve_window("month", anchor, tz, now)
    assert s.date() == date(2026, 7, 1) and e.date() == date(2026, 8, 1) and label == "2026-07"
    s, e, label, _ = resolve_window("year", anchor, tz, now)
    assert s.date() == date(2026, 1, 1) and e.date() == date(2027, 1, 1) and label == "2026"


def test_resolve_window_december_month_rolls_the_year():
    _, e, _, _ = resolve_window("month", date(2026, 12, 15), AMS, datetime(2027, 1, 2, tzinfo=AMS))
    assert e.date() == date(2027, 1, 1)


def test_resolve_window_not_partial_for_a_past_period():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=AMS)
    _, _, _, partial = resolve_window("day", date(2026, 6, 1), AMS, now)
    assert partial is False


def test_build_report_has_flows_and_three_scores():
    start = datetime(2026, 6, 28, tzinfo=UTC)
    end = start + timedelta(days=1)
    raw = [{"ts": (start + timedelta(hours=12)).isoformat(), "grid_power_w": -1000,
            "solar_power_w": 3000, "battery_power_w": -1000, "ev_power_w": 0.0, "soc_pct": 50.0}]
    der = [{"ts": (start + timedelta(hours=12)).isoformat(), "house_load_w": 1000,
            "non_ev_load_w": 1000}]
    prices = [_P(start + timedelta(hours=h), round(0.10 + 0.01 * h, 3)) for h in range(24)]
    r = build_report(raw, der, prices, period="day", start=start, end=end,
                     label="2026-06-28", partial=False, grid_factor=0.27)
    assert r.period == "day" and r.flows["has_data"] is True
    assert {s["key"] for s in r.scores} == {"self_consumption", "co2", "best_price"}
    # Every score has value/raw/unit/explanation fields.
    assert all({"value", "raw", "unit", "explanation"} <= set(s) for s in r.scores)


def _seed(db: str) -> None:
    async def go():
        store = HistoryStore(db)
        await store.init()
        await store.record(PAST.isoformat(), RawSample(-1000, 3000, -1000, 0.0, 50.0),
                           DerivedSample(1000, 1000))
        await store.record((PAST + timedelta(hours=6)).isoformat(),
                           RawSample(800, 0, 0, 0.0, 50.0), DerivedSample(800, 800))
    asyncio.run(go())


def _app(db: str):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db), settings_store=SettingsStore(db),
        price_source=MockPriceSource(AMS),
    )


def test_report_endpoint_returns_day_report(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2026-06-28").json()
    assert b["period"] == "day" and b["flows"]["has_data"] is True
    assert {s["key"] for s in b["scores"]} == {"self_consumption", "co2", "best_price"}
    assert b["label"] == "2026-06-28"


def test_report_endpoint_validation_and_future(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        assert c.get("/api/report?period=day&date=not-a-date").status_code == 422
        assert c.get("/api/report?period=decade").status_code == 422  # bad period → pattern reject
        fut = c.get("/api/report?period=day&date=2099-01-01").json()
        assert fut["flows"]["has_data"] is False  # future window → honest empty, not an error
