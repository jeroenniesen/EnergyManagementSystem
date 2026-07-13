"""GET /api/digest (BACKLOG B-58 "the Sunday read"): the week's saved €, what the system did, one
tweak — gathered from a seeded week of history + audit rows. No hardware.

MONDAY (2026-06-29) is a fully-completed past Mon-Sun week relative to any real "now" this suite
runs under (see test_finance.py / test_reporting.py for the same convention)."""
import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.audit import AuditStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import _last_completed_week_monday, create_app

AMS = ZoneInfo("Europe/Amsterdam")
MONDAY = datetime(2026, 6, 29, 0, 0, tzinfo=AMS)


def _seed_week(db: str) -> None:
    async def go():
        store = HistoryStore(db)
        await store.init()
        for day in range(7):
            local_noon = MONDAY + timedelta(days=day, hours=12)
            ts = local_noon.astimezone(UTC).isoformat()
            raw = RawSample(grid_power_w=-500.0, solar_power_w=2000.0, battery_power_w=-500.0,
                            ev_power_w=0.0, soc_pct=60.0)
            await store.record(ts, raw, reconstruct(raw))
            await store.upsert_price_slots([(ts, 0.20)])
    asyncio.run(go())


def _app(db: str, *, audit_store: AuditStore | None = None):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db), settings_store=SettingsStore(db),
        price_source=MockPriceSource(AMS), audit_store=audit_store,
    )


def test_digest_endpoint_returns_the_requested_week(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_week(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/digest?week=2026-07-01").json()  # any day inside the week
    assert b["week_label"] == "Week of 2026-06-29"
    assert b["days_measured"] == 7
    assert b["days_total"] == 7
    assert b["saved_eur"] is not None
    assert isinstance(b["headline"], str) and b["headline"]
    # No advisor evidence seeded -> the null-tweak case is None (calm = absence; the headline
    # tail carries "settings look right").
    assert b["tweak"] is None
    assert b["self_sufficiency_pct"] is not None
    assert b["solar_kwh"] > 0


def test_digest_endpoint_rejects_a_malformed_week_param(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db)) as c:
        assert c.get("/api/digest?week=not-a-date").status_code == 422


def test_digest_endpoint_default_is_the_last_completed_week(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db)) as c:
        b = c.get("/api/digest").json()
    now_local = datetime.now(UTC).astimezone(AMS)
    expected = _last_completed_week_monday(now_local)
    assert b["week_label"] == f"Week of {expected.isoformat()}"


def test_digest_endpoint_without_a_store_is_still_a_valid_empty_digest():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS)
    with TestClient(app) as c:
        b = c.get("/api/digest?week=2026-07-01").json()
    assert b["week_label"] == "Week of 2026-06-29"
    assert b["saved_eur"] is None
    assert b["days_measured"] == 0 and b["days_total"] == 0


def test_digest_endpoint_counts_actions_from_the_weeks_audit_log(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_week(db)
    audit_store = AuditStore(db)
    asyncio.run(audit_store.init())

    async def seed_audit():
        in_week = (MONDAY + timedelta(days=2, hours=10)).astimezone(UTC).isoformat()
        before_week = (MONDAY - timedelta(days=1)).astimezone(UTC).isoformat()
        await audit_store.append(
            in_week, "battery_decision",
            "Battery mode auto → discharge_for_load — command sent",
            {"reason": "discharge: €0.35/kWh > break-even €0.20"})
        await audit_store.append(
            in_week, "manual_override", "Manual override: hold_reserve for 30 min", {})
        # Outside the window — must NOT be counted.
        await audit_store.append(
            before_week, "battery_decision",
            "Battery mode auto → discharge_for_load — command sent", {})
    asyncio.run(seed_audit())

    with TestClient(_app(db, audit_store=audit_store)) as c:
        b = c.get("/api/digest?week=2026-07-01").json()
    assert b["actions"] == {"mode_switches": 1, "negative_soaks": 0, "overrides": 1}


# --- _last_completed_week_monday: the Mon-Sun boundary, pure and tz-aware -----------------------

def test_last_completed_week_monday_just_after_midnight_on_monday():
    # The week that just ended (06-29..07-05) is NOW fully completed.
    now_local = datetime(2026, 7, 6, 0, 0, 1, tzinfo=AMS)
    assert _last_completed_week_monday(now_local) == date(2026, 6, 29)


def test_last_completed_week_monday_late_sunday_night_still_the_prior_week():
    # 2026-07-05 is a Sunday — the current week (06-29..07-05) hasn't finished until midnight.
    now_local = datetime(2026, 7, 5, 23, 59, tzinfo=AMS)
    assert _last_completed_week_monday(now_local) == date(2026, 6, 22)


def test_last_completed_week_monday_mid_week():
    now_local = datetime(2026, 7, 8, 12, 0, tzinfo=AMS)  # a Wednesday
    assert _last_completed_week_monday(now_local) == date(2026, 6, 29)
