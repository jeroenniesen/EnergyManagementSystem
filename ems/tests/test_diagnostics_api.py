from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def test_diagnostics_minimal_app_reports_checks():
    # No stores/sources wired -> still returns a structured report, with warnings (not a crash).
    b = TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")).get(
        "/api/diagnostics"
    ).json()
    assert "overall" in b
    keys = {c["key"] for c in b["checks"]}
    assert {"mode", "history_store", "prices", "battery", "data_quality", "auth"} <= keys


def test_diagnostics_survives_unreachable_battery():
    # A battery whose probe() raises must show as a warn check, not crash the endpoint (500).
    class _BadBattery:
        def probe(self):
            raise RuntimeError("battery unreachable")

    app = create_app(MockSource(), dry_run=True, dev_mode="mock", battery=_BadBattery())
    r = TestClient(app).get("/api/diagnostics")
    assert r.status_code == 200
    battery = next(c for c in r.json()["checks"] if c["key"] == "battery")
    assert battery["status"] == "warn"


def test_diagnostics_fully_wired_app_is_healthy(tmp_path):
    from datetime import UTC, datetime

    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for sig in SIGNALS:
        fr.mark(sig, now)
    ams = ZoneInfo("Europe/Amsterdam")
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        store=HistoryStore(str(tmp_path / "ems.sqlite")),
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
        freshness=fr,
        price_source=MockPriceSource(ams),
        solar_forecast=MockSolarForecastSource(ams),
        battery=MockBatteryDriver(),
    )
    with TestClient(app) as c:
        b = c.get("/api/diagnostics").json()
    assert b["overall"] == "ok"
    store = next(x for x in b["checks"] if x["key"] == "history_store")
    assert store["status"] == "ok"


def test_diagnostics_warns_when_car_guard_is_blind(tmp_path):
    # The car-charging guard can't fire without an EV meter. On + live + no EV meter must surface a
    # warn check (the silent-misconfiguration that lets the battery discharge into the car).
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(), dry_run=True, dev_mode="live", store=HistoryStore(db),
        settings_store=SettingsStore(db),
    )
    with TestClient(app) as c:
        # hold_battery_when_car_charging defaults on; meters.car_ip is blank → blind.
        checks = c.get("/api/diagnostics").json()["checks"]
    guard = next((x for x in checks if x["key"] == "car_guard"), None)
    assert guard is not None and guard["status"] == "warn"
    assert "EV meter" in guard["detail"]


def test_diagnostics_no_car_guard_warning_in_mock_mode(tmp_path):
    # In mock/dev mode the guard-blind warning is irrelevant (no real devices) — don't nag.
    db = str(tmp_path / "ems.sqlite")
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", store=HistoryStore(db),
                     settings_store=SettingsStore(db))
    with TestClient(app) as c:
        checks = c.get("/api/diagnostics").json()["checks"]
    assert not any(x["key"] == "car_guard" for x in checks)


def test_diagnostics_exposes_long_run_storage_and_recorder_health(tmp_path):
    # Long-running review: the operator must be able to SEE DB/WAL size, row counts, and recorder
    # health (so a stuck recorder / growing DB is visible, not inferred from stale data).
    from ems.sense import Recorder

    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", store=store,
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
        recorder=Recorder(MockSource(), store, fr, cycle_seconds=999),
    )
    with TestClient(app) as c:  # lifespan takes one startup sample → recorder reports success
        b = c.get("/api/diagnostics").json()
    assert b["storage"] is not None
    assert {"db_bytes", "wal_bytes", "raw_rows", "derived_rows"} <= set(b["storage"])
    assert b["recorder"] is not None
    assert {"last_success_at", "consecutive_failures", "last_error"} <= set(b["recorder"])
    assert b["recorder"]["consecutive_failures"] == 0  # startup sample succeeded


def test_diagnostics_exposes_history_store_self_heal_stats(tmp_path):
    # B-49: the storage block carries the recorder's consecutive persist-failure streak + the last
    # time the history store had to re-heal a dead connection, so the System page can surface it.
    from ems.sense import Recorder

    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", store=store,
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
        recorder=Recorder(MockSource(), store, fr, cycle_seconds=999),
    )
    with TestClient(app) as c:
        b = c.get("/api/diagnostics").json()
    hs = b["storage"]["history_store"]
    assert set(hs) == {"consecutive_persist_failures", "last_reheal_iso"}
    assert hs["consecutive_persist_failures"] == 0  # startup sample succeeded
    assert hs["last_reheal_iso"] is None  # never had to re-heal


def test_diagnostics_exposes_canonical_forecast_job_state(tmp_path):
    # The 18:00 canonical-forecast job (design §4.3) is otherwise invisible when dead — its state
    # box must ride along in /api/diagnostics under storage.canonical_forecast, same shape as the
    # backup state, so a freshly-started app (no cycle has fired yet) reports the "never run" shape.
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        store=HistoryStore(str(tmp_path / "ems.sqlite")),
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
    )
    with TestClient(app) as c:
        b = c.get("/api/diagnostics").json()
    assert b["storage"] is not None
    assert "canonical_forecast" in b["storage"]
    assert b["storage"]["canonical_forecast"] == {
        "last_success_date": None, "last_attempt_iso": None, "ok": None,
    }
