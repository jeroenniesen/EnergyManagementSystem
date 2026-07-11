"""Export-package assembly (pure): CSV serialisation, ZIP packing, manifest."""
import json

from ems.export_package import (
    AUDIT_COLUMNS,
    RAW_COLUMNS,
    build_manifest,
    build_zip,
    read_member,
    rows_to_csv,
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


# ---- endpoint: GET /api/export/package returns a ZIP of the CSVs + manifest ----
import asyncio  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from ems.domain import RawSample  # noqa: E402
from ems.load_model import reconstruct  # noqa: E402
from ems.sources.mock import MockSource  # noqa: E402
from ems.sources.prices import MockPriceSource  # noqa: E402
from ems.storage.audit import AuditStore  # noqa: E402
from ems.storage.history import HistoryStore  # noqa: E402
from ems.storage.settings import SettingsStore  # noqa: E402
from ems.web.api import create_app  # noqa: E402

AMS = ZoneInfo("Europe/Amsterdam")


def _seed(db: str) -> None:
    async def go():
        store = HistoryStore(db)
        await store.init()
        raw = RawSample(grid_power_w=1600.0, solar_power_w=3500.0, battery_power_w=-800.0,
                        ev_power_w=4000.0, soc_pct=55.0)
        await store.record("2026-06-28T10:00:00+00:00", raw, reconstruct(raw))
        await store.upsert_price_slots([("2026-06-28T10:00:00+00:00", 0.18)])
        await store.upsert_daily_finance("2026-06-28", {"day": "2026-06-28", "has_data": True,
                                                         "saved_eur": 0.42, "price_coverage": 1.0})
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
    assert {"raw_samples.csv", "derived_samples.csv", "prices.csv", "daily_finance.csv",
            "audit_log.csv", "manifest.json"} <= names
    # Real data made it in.
    assert "1600.0" in read_member(data, "raw_samples.csv")
    assert "0.18" in read_member(data, "prices.csv")
    assert "0.42" in read_member(data, "daily_finance.csv")
    assert "allow_self_consumption" in read_member(data, "audit_log.csv")
    manifest = json.loads(read_member(data, "manifest.json"))
    assert manifest["kind"] == "ems-export-package"
    assert manifest["counts"]["raw_samples"] == 1
    assert manifest["counts"]["prices"] == 1
