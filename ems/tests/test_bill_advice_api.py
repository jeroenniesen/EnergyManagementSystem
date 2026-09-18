from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.web.api import create_app


def test_advice_reports_insufficient_evidence_without_history():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", tz=ZoneInfo("UTC"))
    with TestClient(app) as client:
        r = client.get("/api/bill-advice")
        assert r.status_code == 200
        assert r.json()["reserve"]["available"] is False
        assert r.json()["calibration"]["available"] is False
        assert r.json()["automatic"] is False


def test_appliance_api_is_read_only_and_validates_deadlines():
    tz = ZoneInfo("UTC")
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=tz, price_source=MockPriceSource(tz)
    )
    with TestClient(app) as client:
        body = {
            "duration_minutes": 60,
            "energy_kwh": 1,
            "deadline": (datetime.now(UTC) + timedelta(hours=12)).isoformat(),
        }
        result = client.post("/api/advisor/appliance", json=body)
        assert result.status_code == 200
        assert result.json()["automatic"] is False
        body["deadline"] = "2020-01-01T00:00:00+00:00"
        assert client.post("/api/advisor/appliance", json=body).status_code == 422
    assert "/api/advisor/appliance" in app.state.write_exempt_paths


def test_new_economics_defaults_off_and_tariff_append_requires_operate():
    from ems.settings import effective_settings
    from ems.web.authz import Tier, required_tier

    assert effective_settings({})["planner.bill_optimization_enabled"] is False
    assert required_tier("/api/tariffs", "POST") == Tier.OPERATE
    assert required_tier("/api/invoice-reconciliation", "POST") == Tier.VIEW


def test_tariff_periods_survive_settings_reload():
    from ems.settings import effective_settings

    stored = {"tariffs.periods": [], "tariffs.legacy": {"export_price_model": "fixed"}}
    result = effective_settings(stored)
    assert result["tariffs.legacy"] == stored["tariffs.legacy"]


def test_finance_reprices_cached_day_when_tariff_period_added(tmp_path):
    import asyncio

    from ems.domain import RawSample
    from ems.load_model import reconstruct
    from ems.storage.history import HistoryStore
    from ems.storage.settings import SettingsStore

    db = str(tmp_path / "history.sqlite")
    history = HistoryStore(db)

    async def seed():
        await history.init()
        raw = RawSample(
            grid_power_w=1000, solar_power_w=0, battery_power_w=0, ev_power_w=0, soc_pct=50
        )
        await history.record("2026-06-28T12:00:00+00:00", raw, reconstruct(raw))
        await history.upsert_price_slots([("2026-06-28T12:00:00+00:00", 0.2)])

    asyncio.run(seed())
    app = create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        tz=ZoneInfo("UTC"),
        store=history,
        settings_store=SettingsStore(db),
    )
    with TestClient(app) as client:
        before = client.get("/api/finance?period=day&date=2026-06-28").json()["days"][0]
        r = client.post(
            "/api/tariffs",
            json={
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "raw_includes_import_components": False,
                "import_tax_eur_per_kwh": 0.3,
                "import_surcharge_eur_per_kwh": 0,
                "export_tax_eur_per_kwh": 0,
                "export_surcharge_eur_per_kwh": 0,
                "export_fee_eur_per_kwh": 0,
            },
        )
        assert r.status_code == 200
        after = client.get("/api/finance?period=day&date=2026-06-28").json()["days"][0]
        assert after["grid_cost_eur"] > before["grid_cost_eur"]


def test_finance_keeps_archive_when_only_partial_raw_detail_remains(tmp_path):
    import asyncio

    from ems.domain import RawSample
    from ems.load_model import reconstruct
    from ems.storage.history import HistoryStore
    db = str(tmp_path / "archive.sqlite")
    history = HistoryStore(db)

    async def seed():
        await history.init()
        raw = RawSample(grid_power_w=1000, solar_power_w=0, battery_power_w=0,
                        ev_power_w=0, soc_pct=50)
        await history.record("2026-06-28T12:00:00+00:00", raw, reconstruct(raw))
        await history.upsert_price_slots([("2026-06-28T12:00:00+00:00", .2)])
        await history.upsert_daily_finance("2026-06-28", {
            "day": "2026-06-28", "has_data": True, "calc_v": 1,
            "sample_coverage": 1.0, "price_coverage": 1.0,
            "grid_cost_eur": 4.8, "grid_import_kwh": 24.0, "saved_eur": 0,
        })
    asyncio.run(seed())
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", tz=ZoneInfo("UTC"), store=history)
    with TestClient(app) as client:
        row = client.get("/api/finance?period=day&date=2026-06-28").json()["days"][0]
        assert row["grid_import_kwh"] == 24
        assert "retained" in row["calculation_note"]
