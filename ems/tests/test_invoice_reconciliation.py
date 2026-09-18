from datetime import UTC, datetime, timedelta

import pytest

from ems import finance
from ems.storage.settings import SettingsStore


def test_invoice_complete_and_partial_observations():
    start = datetime(2027, 1, 1, tzinfo=UTC)
    rows = [{"ts": start.isoformat(), "grid_power_w": 1000, "battery_power_w": 0}]
    prices = [{"start_ts": start.isoformat(), "eur_per_kwh": 0.20}]
    result = finance.reconcile_invoice(
        rows,
        prices,
        start=start,
        end=start + timedelta(minutes=15),
        invoice_eur=2.05,
        fixed_cost_eur=2.0,
        sample_interval_seconds=900.0,
    )
    assert result["estimated_total_eur"] == pytest.approx(2.05)
    assert result["difference_eur"] == pytest.approx(0.0)
    partial = finance.reconcile_invoice(
        rows,
        prices,
        start=start,
        end=start + timedelta(minutes=30),
        invoice_eur=2.05,
        fixed_cost_eur=2.0,
        sample_interval_seconds=900.0,
    )
    assert partial["estimated_total_eur"] is None
    assert partial["difference_eur"] is None
    assert partial["observed_variable_cost_eur"] == pytest.approx(0.05)
    assert partial["sample_coverage"] == 0.5
    with pytest.raises(ValueError):
        finance.reconcile_invoice(
            rows,
            prices,
            start=start,
            end=start + timedelta(minutes=15),
            invoice_eur=float("inf"),
            fixed_cost_eur=2.0,
        )


def test_append_preserves_legacy_and_rejects_overlap(tmp_path):
    import asyncio

    asyncio.run(_append_preserves_legacy(tmp_path))


async def _append_preserves_legacy(tmp_path):
    from ems.tariff_history import append_period, finance_tariff_kwargs
    from ems.tests.test_tariff_periods import period

    store = SettingsStore(tmp_path / "settings.db")
    await store.init()
    settings = {"prices.energy_tax_eur_per_kwh": 0.12}
    try:
        await append_period(store, settings, period())
        settings["prices.energy_tax_eur_per_kwh"] = 0.99
        persisted = await store.all()
        assert persisted["tariffs.legacy"]["energy_tax_eur_per_kwh"] == 0.12
        assert finance_tariff_kwargs(persisted)["energy_tax_eur_per_kwh"] == 0.12
        with pytest.raises(ValueError):
            await append_period(store, settings, period())
        assert len((await store.all())["tariffs.periods"]) == 1
    finally:
        await store.close()


def test_routes_persist_explicit_period_and_reconcile_without_mutation(tmp_path):
    import asyncio
    from dataclasses import asdict
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ems.storage.audit import AuditStore
    from ems.tests.test_tariff_periods import period
    from ems.web.routes.tariffs import build_router

    store = SettingsStore(tmp_path / "settings.db")
    asyncio.run(store.init())
    audit = AuditStore(str(tmp_path / "audit.db"))
    asyncio.run(audit.init())
    ctx = SimpleNamespace(
        settings_cache={}, store=None, site_tz=ZoneInfo("Europe/Amsterdam"), audit_store=audit
    )
    app = FastAPI()
    app.include_router(build_router(ctx, store))
    try:
        with TestClient(app) as client:
            assert client.post("/api/tariffs", json=asdict(period())).status_code == 200
            assert len(client.get("/api/tariffs").json()["periods"]) == 1
            assert len(asyncio.run(audit.recent())) == 1
            assert client.post("/api/tariffs", json=asdict(period())).status_code == 422
            response = client.post(
                "/api/invoice-reconciliation",
                json={
                    "start_date": "2027-01-01",
                    "end_date": "2027-02-01",
                    "invoice_eur": 50,
                    "fixed_cost_eur": 10,
                },
            )
            assert response.status_code == 200
            assert response.json()["estimated_total_eur"] is None
            assert len(client.get("/api/tariffs").json()["periods"]) == 1
            assert len(asyncio.run(audit.recent())) == 1
    finally:
        asyncio.run(store.close())
        asyncio.run(audit.close())


def test_invoice_route_tolerates_normal_recorder_jitter():
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ems.web.routes.tariffs import build_router

    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "ts": (start + timedelta(seconds=i * 300.1)).isoformat(),
            "grid_power_w": 1000,
            "battery_power_w": 0,
        }
        for i in range(288)
    ]
    prices = [
        {"start_ts": (start + timedelta(minutes=15 * i)).isoformat(), "eur_per_kwh": 0.2}
        for i in range(96)
    ]

    class History:
        async def raw_between(self, *args, **kwargs):
            return rows

        async def prices_between(self, *args, **kwargs):
            return prices

    ctx = SimpleNamespace(
        settings_cache={},
        store=History(),
        site_tz=ZoneInfo("UTC"),
        sample_cadence_seconds=lambda: 300.0,
    )
    app = FastAPI()
    app.include_router(build_router(ctx, None))
    with TestClient(app) as client:
        body = dict(
            start_date="2026-01-01", end_date="2026-01-02", invoice_eur=4.8, fixed_cost_eur=0
        )
        result = client.post("/api/invoice-reconciliation", json=body).json()
        assert result["complete"]
        assert result["estimated_total_eur"] == pytest.approx(4.8)
        del rows[50:55]
        assert not client.post("/api/invoice-reconciliation", json=body).json()["complete"]


@pytest.mark.parametrize("role,expected", [("reader", 403), ("user", 200)])
def test_tariff_write_permissions_and_read_only_invoice(tmp_path, role, expected):
    from dataclasses import asdict

    from fastapi.testclient import TestClient

    from ems.tests.test_auth_api import _app, _seed_user
    from ems.tests.test_tariff_periods import period

    db = str(tmp_path / "auth.db")
    _seed_user(db, "person", "pw12345678", role)
    with TestClient(_app(db)) as client:
        assert client.get("/api/tariffs").status_code == 401
        assert client.post("/api/tariffs", json=asdict(period())).status_code == 401
        login = client.post(
            "/api/auth/login", json={"username": "person", "password": "pw12345678"}
        )
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        assert client.get("/api/tariffs", headers=headers).status_code == 200
        assert (
            client.post("/api/tariffs", headers=headers, json=asdict(period())).status_code
            == expected
        )
        result = client.post(
            "/api/invoice-reconciliation",
            headers=headers,
            json={
                "start_date": "2020-01-01",
                "end_date": "2020-01-02",
                "invoice_eur": 5,
                "fixed_cost_eur": 1,
            },
        )
        assert result.status_code == 200
        assert result.json()["estimated_total_eur"] is None
