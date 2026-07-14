"""GET /api/accuracy: all three forecast/prediction-accuracy tracks (B-72) in one read-only call."""
import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _app(db: str):
    return create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
                       store=HistoryStore(db))


def _seed_solar_evidence(store: HistoryStore, now: datetime) -> None:
    # 48 matched daytime slots within the last 14 days (the endpoint's solar window), 12 each of
    # ratio 0.7/0.8/0.9/1.0 — same shape as test_export_package.py's _seed_solar_evidence.
    # Seeded as CANONICAL (canonical=1) prediction-ledger rows — /api/accuracy's solar track now
    # scores `ledger_canonical_between('solar', ...)`, the single scoring source (design §3.3).
    anchor = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    ratios = [0.7, 0.8, 0.9, 1.0]
    for i in range(48):
        ts = (anchor - timedelta(minutes=15 * i)).isoformat()
        solar_w = 1000.0 * ratios[i % 4]
        raw = RawSample(grid_power_w=100.0, solar_power_w=solar_w, battery_power_w=0.0,
                        ev_power_w=0.0, soc_pct=50.0)
        asyncio.run(store.record(ts, raw, reconstruct(raw)))
        asyncio.run(store.ledger_append(
            [(ts, "solar", ts, 500.0, 1000.0, 1500.0, "test", None, None, 1)]))


def _seed_plan_execution_evidence(store: HistoryStore, now: datetime) -> None:
    # 3 unique deadlines, well inside the last 60 days, each with a target row and an achieved
    # row 5 minutes after the deadline (well inside the 30-min grace window).
    for days_ago in (10, 9, 8):
        day = now - timedelta(days=days_ago)
        deadline = day.replace(hour=18, minute=0, second=0, microsecond=0)
        target_ts = (deadline - timedelta(hours=1)).isoformat()
        achieved_ts = (deadline + timedelta(minutes=5)).isoformat()
        asyncio.run(store.record_plan(target_ts, {
            "strategy": "winter", "target_soc": 80.0, "deadline": deadline.isoformat(),
            "soc_pct": 60.0, "intent": "grid_charge_to_target",
        }))
        asyncio.run(store.record_plan(achieved_ts, {
            "strategy": "winter", "target_soc": None, "deadline": None,
            "soc_pct": 82.0, "intent": "allow_self_consumption",
        }))


def _seed_load_baseline_evidence(store: HistoryStore, now: datetime) -> None:
    # 6 hour-buckets x 8 weekly occurrences, all inside the last 60 days -> 5 evaluable hours per
    # bucket (indices 3..7, needing >= 3 priors) = 30 evaluable hours, above the 24 minimum.
    anchor = now - timedelta(days=50)
    for hour in (3, 7, 11, 15, 19, 23):
        for week in range(8):
            ts = (anchor + timedelta(days=7 * week, hours=hour)).isoformat()
            raw = RawSample(grid_power_w=500.0, solar_power_w=0.0, battery_power_w=0.0,
                            ev_power_w=0.0, soc_pct=50.0)
            asyncio.run(store.record(ts, raw, reconstruct(raw)))


def test_accuracy_endpoint_returns_all_three_tracks_with_enough_evidence(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    store = HistoryStore(db)
    asyncio.run(store.init())
    now = datetime.now(UTC)
    _seed_solar_evidence(store, now)
    _seed_plan_execution_evidence(store, now)
    _seed_load_baseline_evidence(store, now)

    with TestClient(_app(db)) as c:
        body = c.get("/api/accuracy").json()

    assert body["solar"] is not None
    assert body["solar"]["n_slots"] == 48
    assert body["solar"]["bias_w"] is not None

    assert body["plan_execution"] is not None
    assert body["plan_execution"]["n_deadlines"] == 3

    assert body["load"] is not None
    assert body["load"]["n_hours"] == 30

    # B-76: with solid evidence across all three tracks and nothing amiss, health reads all-ok.
    assert body["health"] == {"solar": "ok", "load": "ok", "plan_execution": "ok", "notes": []}


def test_accuracy_endpoint_returns_nulls_without_enough_evidence(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db)) as c:  # fresh store — no evidence for any track
        body = c.get("/api/accuracy").json()
    # solar (forecast_error) always returns a dict, even with zero matched slots — only
    # plan_execution/load (which CAN return None below their evidence minimum) are null here.
    assert body["solar"]["n_slots"] == 0
    assert body["plan_execution"] is None
    assert body["load"] is None
    # B-76: no evidence anywhere yet reads as an honest 'unknown' per track, never alarming.
    assert body["health"] == {
        "solar": "unknown", "load": "unknown", "plan_execution": "unknown", "notes": [],
    }


def test_accuracy_endpoint_returns_nulls_without_a_store():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS)
    with TestClient(app) as c:
        body = c.get("/api/accuracy").json()
    assert body == {
        "solar": None, "plan_execution": None, "load": None,
        "health": {"solar": "unknown", "load": "unknown", "plan_execution": "unknown",
                   "notes": []},
    }


def test_accuracy_endpoint_shape_has_exactly_the_four_keys(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db)) as c:
        body = c.get("/api/accuracy").json()
    assert set(body.keys()) == {"solar", "plan_execution", "load", "health"}
