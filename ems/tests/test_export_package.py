"""Export-package assembly (pure): CSV serialisation, ZIP packing, manifest."""
import json

from ems.export_package import (
    AUDIT_COLUMNS,
    RAW_COLUMNS,
    build_manifest,
    build_zip,
    incident_rollup,
    read_member,
    rows_to_csv,
    validation_summary,
    zip_names,
)


def test_rows_to_csv_has_header_and_ignores_unknown_keys():
    rows = [{"ts": "2026-06-28T10:00:00+00:00", "grid_power_w": 200.0, "solar_power_w": 0.0,
             "battery_power_w": -100.0, "ev_power_w": 0.0, "soc_pct": 55.0, "extra": "drop me"}]
    out = rows_to_csv(rows, RAW_COLUMNS)
    lines = out.strip().splitlines()
    assert lines[0] == ",".join(RAW_COLUMNS)  # stable header
    assert "drop me" not in out               # unknown column ignored
    assert "200.0" in lines[1]


def test_rows_to_csv_json_encodes_dict_cells():
    # An audit detail is a dict — it must be JSON-encoded so the CSV stays one cell.
    rows = [{"id": 1, "ts": "t", "category": "battery_decision", "summary": "set auto",
             "detail": {"intent": "allow_self_consumption", "confirmed": True}}]
    out = rows_to_csv(rows, AUDIT_COLUMNS)
    assert '"intent"' in out and "allow_self_consumption" in out
    assert out.strip().count("\n") == 1  # header + exactly one data row (no embedded newline break)


def test_rows_to_csv_empty_still_writes_header():
    assert rows_to_csv([], RAW_COLUMNS).strip() == ",".join(RAW_COLUMNS)


def test_build_zip_roundtrips_members_sorted_and_deterministic():
    members = {"b.csv": "hello", "a.csv": "world"}
    data = build_zip(members)
    assert zip_names(data) == ["a.csv", "b.csv"]           # sorted
    assert read_member(data, "a.csv") == "world"
    assert build_zip(members) == data                       # deterministic bytes


def test_build_manifest_carries_window_counts_and_extra():
    m = json.loads(build_manifest(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window_start="2026-05-28T00:00:00+00:00", window_end="2026-06-28T12:00:00+00:00",
        counts={"raw_samples": 100, "prices": 96},
        extra={"diagnostics": {"ready": True}},
    ))
    assert m["kind"] == "ems-export-package" and m["schema_version"] == 1
    assert m["app_version"] == "0.0.1"
    assert m["counts"]["raw_samples"] == 100
    assert m["window"]["start"].startswith("2026-05-28")
    assert m["diagnostics"] == {"ready": True}  # extra merged in


# ---- incident_rollup: control-health incidents rolled up from the audit log ----

def test_incident_rollup_classifies_counts_and_dates():
    rows = [
        {"id": 1, "ts": "2026-06-20T10:00:00+00:00", "category": "battery_decision",
         "summary": "Battery cluster MISMATCH — 1 tower(s) NOT following the commanded mode",
         "detail": {}},
        {"id": 2, "ts": "2026-06-25T09:00:00+00:00", "category": "battery_decision",
         "summary": "Battery charge unconfirmed — device slow to respond; holding and retrying "
                    "(not reverting)",
         "detail": {}},
        {"id": 3, "ts": "2026-06-26T09:00:00+00:00", "category": "battery_decision",
         "summary": "Battery discharge unconfirmed — device slow to respond; holding and "
                    "retrying (not reverting)",
         "detail": {}},
        {"id": 4, "ts": "2026-06-26T12:00:00+00:00", "category": "battery_decision",
         "summary": "Would set battery to charge — cheap window", "detail": {}},  # benign, ignored
    ]
    out = incident_rollup(rows)
    assert out["total"] == 3
    assert out["by_type"] == {"cluster_mismatch": 1, "command_failed": 2}
    assert out["by_day"] == {"2026-06-20": 1, "2026-06-25": 1, "2026-06-26": 1}
    assert out["most_recent"] == "2026-06-26T09:00:00+00:00"
    assert out["last_7_days"] == 3  # newest day minus 6/20 = 6 days -> all three within 7


def test_incident_rollup_empty_input_is_zeros():
    out = incident_rollup([])
    assert out == {"total": 0, "by_type": {}, "by_day": {}, "most_recent": None, "last_7_days": 0}


def test_incident_rollup_priority_order_first_match_wins():
    # Contains both "unconfirmed" and "reverted" -> command_failed wins (checked before revert).
    rows = [{"ts": "2026-06-28T10:00:00+00:00", "summary":
             "Battery charge unconfirmed -> reverted to AUTO", "detail": {}}]
    out = incident_rollup(rows)
    assert out["by_type"] == {"command_failed": 1}


def test_incident_rollup_fallback_and_revert_and_detail_text():
    rows = [
        {"ts": "2026-06-01T00:00:00+00:00", "summary": "Held plan",
         "detail": {"reason": "prices unavailable — using failsafe curve"}},
        {"ts": "2026-06-02T00:00:00+00:00", "summary": "Reverted battery mode to AUTO",
         "detail": {}},
    ]
    out = incident_rollup(rows)
    assert out["by_type"] == {"fallback": 1, "revert": 1}
    assert out["total"] == 2


def test_incident_rollup_last_7_days_excludes_older_incidents():
    rows = [
        {"ts": "2026-06-01T00:00:00+00:00", "summary": "Battery cluster mismatch", "detail": {}},
        {"ts": "2026-06-20T00:00:00+00:00", "summary": "Battery cluster mismatch", "detail": {}},
    ]
    out = incident_rollup(rows)
    assert out["total"] == 2
    assert out["last_7_days"] == 1  # only the 6/20 incident is within 7 days of itself


def test_validation_summary_includes_incidents_section():
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None,
        validation={"incidents": {"total": 2, "last_7_days": 1,
                                   "most_recent": "2026-06-26T09:00:00+00:00",
                                   "by_type": {"cluster_mismatch": 1, "command_failed": 1},
                                   "by_day": {}}},
    )
    assert "Incidents" in text
    assert "Total:          2 (last 7 days: 1)" in text
    assert "cluster_mismatch=1" in text and "command_failed=1" in text
    assert "2026-06-26T09:00:00+00:00" in text


# ---- endpoint: GET /api/export/package returns a ZIP of the CSVs + manifest ----
import asyncio  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from ems.domain import RawSample  # noqa: E402
from ems.load_model import reconstruct  # noqa: E402
from ems.sources.mock import MockSource  # noqa: E402
from ems.sources.prices import MockPriceSource  # noqa: E402
from ems.storage.audit import AuditStore  # noqa: E402
from ems.storage.history import HistoryStore  # noqa: E402
from ems.storage.settings import SettingsStore  # noqa: E402
from ems.web.api import _FINANCE_CALC_VERSION, create_app  # noqa: E402

AMS = ZoneInfo("Europe/Amsterdam")


def _seed(db: str) -> None:
    async def go():
        store = HistoryStore(db)
        await store.init()
        raw = RawSample(grid_power_w=1600.0, solar_power_w=3500.0, battery_power_w=-800.0,
                        ev_power_w=4000.0, soc_pct=55.0)
        await store.record("2026-06-28T10:00:00+00:00", raw, reconstruct(raw))
        await store.upsert_price_slots([("2026-06-28T10:00:00+00:00", 0.18)])
        await store.upsert_forecast_snapshot(
            "2026-06-28", [("2026-06-28T10:00:00+00:00", 1000.0, 2000.0, 3000.0)])
        # calc_v stamped so the export's backfill (which now runs _ensure_day_finance over every
        # completed day in its window) trusts this as an up-to-date cache hit instead of treating
        # it as stale and recomputing it from the raw sample above (which would overwrite 0.42).
        await store.upsert_daily_finance("2026-06-28", {"day": "2026-06-28", "has_data": True,
                                                         "saved_eur": 0.42, "price_coverage": 1.0,
                                                         "calc_v": _FINANCE_CALC_VERSION})
        await store.record_plan("2026-06-28T10:00:00+00:00", {
            "strategy": "winter", "target_soc": 80.0,
            "deadline": "2026-06-28T18:00:00+00:00", "soc_pct": 55.0,
            "intent": "grid_charge_to_target",
        })
        audit = AuditStore(db)
        await audit.init()
        await audit.append("2026-06-28T10:00:00+00:00", "battery_decision", "set auto",
                           {"intent": "allow_self_consumption", "confirmed": True})
    asyncio.run(go())


def _app(db: str):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS, store=HistoryStore(db),
        settings_store=SettingsStore(db), audit_store=AuditStore(db),
        price_source=MockPriceSource(AMS),
    )


def test_export_package_endpoint_returns_zip_with_all_members(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        r = c.get("/api/export/package?days=400")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers.get("content-disposition", "")
    data = r.content
    names = set(zip_names(data))
    assert {"raw_samples.csv", "derived_samples.csv", "prices.csv", "forecasts.csv",
            "daily_finance.csv", "audit_log.csv", "plan_history.csv", "manifest.json"} <= names
    # Real data made it in.
    assert "1600.0" in read_member(data, "raw_samples.csv")
    assert "0.18" in read_member(data, "prices.csv")
    assert "2000.0" in read_member(data, "forecasts.csv")
    assert "0.42" in read_member(data, "daily_finance.csv")
    assert "allow_self_consumption" in read_member(data, "audit_log.csv")
    assert "grid_charge_to_target" in read_member(data, "plan_history.csv")
    manifest = json.loads(read_member(data, "manifest.json"))
    assert manifest["kind"] == "ems-export-package"
    assert manifest["counts"]["raw_samples"] == 1
    assert manifest["counts"]["prices"] == 1
    assert manifest["counts"]["forecasts"] == 1
    assert manifest["counts"]["plan_history"] == 1


def test_export_package_backfills_daily_finance_for_unviewed_days(tmp_path):
    # /api/finance only computes+stores a completed day's rollup when that day's window is
    # actually VIEWED, so a day nobody looked at is missing from `daily_finance` — the 07-04/07-05
    # -style export gap. Seed 3 consecutive completed days of raw+price history but pre-populate
    # `daily_finance` for only ONE of them (simulating "the user only ever opened one day's
    # finance view"); the export must self-complete the other two before reading the table back.
    # Days are relative to "today" (real wall clock) so the test is stable regardless of when it
    # runs, and a tight window (days=6) keeps the backfill sweep small and its count predictable.
    db = str(tmp_path / "ems.sqlite")
    today = datetime.now(UTC).date()
    day_list = [(today - timedelta(days=n)).isoformat() for n in (4, 3, 2)]  # 3 consecutive days

    async def seed():
        store = HistoryStore(db)
        await store.init()
        for d in day_list:
            ts = f"{d}T12:00:00+00:00"
            raw = RawSample(grid_power_w=500.0, solar_power_w=0.0, battery_power_w=0.0,
                            ev_power_w=0.0, soc_pct=50.0)
            await store.record(ts, raw, reconstruct(raw))
            await store.upsert_price_slots([(ts, 0.20)])
        # Only the FIRST day was ever "viewed" — pre-cached with the CURRENT calc_v, so the
        # backfill must trust it as-is (return the sentinel, not a freshly computed value).
        await store.upsert_daily_finance(day_list[0], {
            "day": day_list[0], "has_data": True, "saved_eur": -0.99, "price_coverage": 1.0,
            "calc_v": _FINANCE_CALC_VERSION,
        })
    asyncio.run(seed())

    with TestClient(_app(db)) as c:
        r = c.get("/api/export/package?days=6")
    assert r.status_code == 200
    data = r.content
    csv_text = read_member(data, "daily_finance.csv")
    for d in day_list:
        assert d in csv_text  # all three days present — the export backfilled the missing two
    assert "-0.99" in csv_text  # the pre-cached day's stored value was trusted, not recomputed

    manifest = json.loads(read_member(data, "manifest.json"))
    assert manifest["counts"]["daily_finance"] >= 3  # at least the 3 target days made it in

    async def rollup():
        s = HistoryStore(db)
        return await s.daily_finance_between(day_list[0], today.isoformat())
    rows = asyncio.run(rollup())
    assert {row["day"] for row in rows} >= set(day_list)  # backfill PERSISTED, not just returned


def test_export_package_backfill_is_best_effort_per_day(tmp_path, monkeypatch):
    # A single day's compute blowing up must not take down the whole export — it's logged and
    # skipped, and the rest of the window still exports (200, with the other days present).
    db = str(tmp_path / "ems.sqlite")
    today = datetime.now(UTC).date()
    day_list = [(today - timedelta(days=n)).isoformat() for n in (4, 3, 2)]
    flaky_day = day_list[1]

    async def seed():
        store = HistoryStore(db)
        await store.init()
        for d in day_list:
            ts = f"{d}T12:00:00+00:00"
            raw = RawSample(grid_power_w=500.0, solar_power_w=0.0, battery_power_w=0.0,
                            ev_power_w=0.0, soc_pct=50.0)
            await store.record(ts, raw, reconstruct(raw))
            await store.upsert_price_slots([(ts, 0.20)])
    asyncio.run(seed())

    import ems.web.api as api_mod
    real_day_finance = api_mod.day_finance

    def flaky_day_finance(raw, price_rows, *, day, degradation_eur_per_kwh):
        if day == flaky_day:
            raise RuntimeError("boom — simulated compute failure")
        return real_day_finance(raw, price_rows, day=day,
                                degradation_eur_per_kwh=degradation_eur_per_kwh)

    monkeypatch.setattr(api_mod, "day_finance", flaky_day_finance)

    with TestClient(_app(db)) as c:
        r = c.get("/api/export/package?days=6")
    assert r.status_code == 200  # one bad day doesn't fail the export
    csv_text = read_member(r.content, "daily_finance.csv")
    assert day_list[0] in csv_text and day_list[2] in csv_text
    assert flaky_day not in csv_text  # the failing day is skipped, not fatal


def test_manifest_carries_validation_payload_and_no_secrets(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package").content
    manifest = json.loads(read_member(data, "manifest.json"))
    # Production-validation payload present.
    assert manifest["operational"]["dry_run"] is True
    assert "timezone" in manifest["operational"]
    assert "strategy.mode" in manifest["config"]              # replay-safe planner knobs
    assert "data_quality" in manifest["health"]
    assert "capability_present" in manifest["health"]
    assert manifest["incidents"] == {  # the seeded audit entry is benign -> zero incidents
        "total": 0, "by_type": {}, "by_day": {}, "most_recent": None, "last_7_days": 0,
    }
    # Privacy: no secrets / IPs / location keys anywhere in the manifest text.
    blob = json.dumps(manifest).lower()
    for leak in ("token", "secret", "_ip", "\"ip\"", "lat", "lon", "password"):
        assert leak not in blob, f"manifest leaked a sensitive key: {leak}"


def test_package_includes_readme_and_validation_summary(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package").content
    assert {"README.md", "validation_summary.txt"} <= set(zip_names(data))
    readme = read_member(data, "README.md")
    assert "+ = importing" in readme and "+ = discharging" in readme   # sign conventions documented
    assert "raw_samples.csv" in readme
    assert "forecasts.csv" in readme
    assert "plan_history.csv" in readme
    summary = read_member(data, "validation_summary.txt")
    assert "Run mode:" in summary and "DRY-RUN" in summary              # run mode legible
    assert "Measured savings over the window: €0.42" in summary         # savings total from finance
    assert "Data quality:" in summary
    assert "Incidents" in summary                                       # control-health section
    assert "manifest.incidents" in readme                               # documented in the README


def test_package_never_leaks_a_stored_secret_value(tmp_path):
    # Definitive redaction check: seed a recognisable secret value + a config-change audit that
    # names the secret KEY, then assert the secret VALUE appears in NO member of the ZIP.
    db = str(tmp_path / "ems.sqlite")
    secret = "S3CRET-TOKEN-DO-NOT-LEAK"

    async def seed_secret():
        settings = SettingsStore(db)
        await settings.init()
        await settings.set_many({"access.web_token": secret, "tibber.token": secret})
        audit = AuditStore(db)
        await audit.init()
        await audit.append("2026-06-28T11:00:00+00:00", "config_change",
                           "Changed 1 setting(s): access.web_token",
                           {"keys": ["access.web_token"], "secrets": ["access.web_token"]})

    _seed(db)
    asyncio.run(seed_secret())
    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package").content
    for name in zip_names(data):
        assert secret not in read_member(data, name), f"secret leaked into {name}"
    # The audit entry is present (by key name), proving we didn't just drop the data.
    assert "access.web_token" in read_member(data, "audit_log.csv")


def test_recorder_wiring_persists_a_plan_history_row_on_startup(tmp_path):
    # End-to-end: create_app wires the `_plan_snapshot` closure onto the recorder
    # (observability-data), so the very first startup tick (recorder.record_now(), called from
    # the app lifespan) should already record one plan_history row.
    from ems.freshness import FreshnessTracker
    from ems.sense import Recorder

    db = str(tmp_path / "ems.sqlite")
    store = HistoryStore(db)
    freshness = FreshnessTracker()
    freshness.register("grid", "solar", "ev", "battery", "soc")
    recorder = Recorder(MockSource(), store, freshness, price_source=MockPriceSource(AMS))
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS, store=store,
        settings_store=SettingsStore(db), audit_store=AuditStore(db),
        price_source=MockPriceSource(AMS), recorder=recorder,
    )
    assert recorder.plan_provider is not None  # wired synchronously inside create_app

    with TestClient(app):
        pass  # triggers lifespan startup, which calls recorder.record_now() once

    rows = asyncio.run(store.plan_history_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))
    assert len(rows) == 1
    assert rows[0]["strategy"] in ("summer", "winter")
    assert rows[0]["soc_pct"] == 55.0  # MockSource's steady-state SoC


def test_create_app_sets_plan_provider_when_recorder_passed(tmp_path):
    # Minimal wiring check (in case a full recorder tick is ever awkward to exercise): passing a
    # recorder must leave it with a callable plan_provider, and omitting one must not error.
    from ems.freshness import FreshnessTracker
    from ems.sense import Recorder

    db = str(tmp_path / "ems.sqlite")
    recorder = Recorder(MockSource(), HistoryStore(db), FreshnessTracker())
    assert recorder.plan_provider is None
    create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS, recorder=recorder)
    assert recorder.plan_provider is not None
    assert callable(recorder.plan_provider)
    # No recorder passed at all → create_app must not require one.
    create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS)


# ---- endpoint: GET /api/incidents — the same rollup, without downloading the export ----

def test_incidents_endpoint_rolls_up_the_audit_log(tmp_path):
    db = str(tmp_path / "ems.sqlite")

    async def seed():
        audit = AuditStore(db)
        await audit.init()
        await audit.append("2026-06-28T09:00:00+00:00", "battery_decision",
                            "Battery cluster MISMATCH — 1 tower(s) NOT following the commanded "
                            "mode", {})
        await audit.append("2026-06-28T10:00:00+00:00", "battery_decision",
                            "Would set battery to charge — cheap window", {})
    asyncio.run(seed())

    with TestClient(_app(db)) as c:
        body = c.get("/api/incidents").json()
    assert body["incidents"]["total"] == 1
    assert body["incidents"]["by_type"] == {"cluster_mismatch": 1}


def test_incidents_endpoint_empty_without_audit_store():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS)
    with TestClient(app) as c:
        body = c.get("/api/incidents").json()
    assert body["incidents"] == {
        "total": 0, "by_type": {}, "by_day": {}, "most_recent": None, "last_7_days": 0,
    }


def test_incident_rollup_matches_hyphenated_failsafe():
    # Runtime text is "fail-safe" (hyphenated); the classifier must still catch it as a fallback.
    from ems.export_package import incident_rollup
    rows = [
        {"ts": "2026-07-06T20:00:00+00:00",
         "summary": "Fell back to AUTO (fail-safe)", "detail": {}},
        {"ts": "2026-07-06T21:00:00+00:00", "summary": "set auto", "detail": {}},  # benign
    ]
    r = incident_rollup(rows)
    assert r["total"] == 1 and r["by_type"].get("fallback") == 1
