"""Setup checklist (#290): status words, readiness layers, live prices, auth on GET."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.authn import hash_password
from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS
from ems.setup_checklist import build_setup_checklist
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.auth import AuthStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def _base(**kw):
    defaults = dict(
        store_ok=True,
        sensing_ok=True,
        plan_ok=True,
        data_quality="complete",
        plan_valid=True,
        operational=False,
        capability_ok=True,
        freshness={"grid": "fresh", "soc": "fresh"},
        battery_ok=True,
        battery_present=True,
        p1_paired=True,
        soc_ready=True,
        prices_live=True,
        forecast_ok=True,
        grid_fuse_unset=False,
    )
    defaults.update(kw)
    return build_setup_checklist(**defaults)


def test_status_werkt_when_watch_inputs_complete():
    cl = _base()
    assert cl.watch_ready is True
    assert cl.headline == "EMS kan meekijken"
    by_key = {i.key: i for i in cl.items}
    assert by_key["battery"].status == "werkt"
    assert by_key["p1"].status == "werkt"
    assert by_key["prices"].status == "werkt"
    assert by_key["battery"].to_dict()["status"] == "Werkt"


def test_status_ontbreekt_blocks_watch_steps():
    cl = _base(battery_ok=False, soc_ready=False, sensing_ok=False)
    by_key = {i.key: i for i in cl.items}
    assert by_key["battery"].status == "ontbreekt"
    assert by_key["battery"].to_dict()["status"] == "Ontbreekt"
    assert cl.steps_remaining == 1
    assert cl.headline == "Nog 1 stap tot EMS kan meekijken"


def test_status_optioneel_for_missing_forecast():
    cl = _base(forecast_ok=False)
    assert next(i for i in cl.items if i.key == "forecast").status == "optioneel"
    assert next(i for i in cl.items if i.key == "forecast").to_dict()["status"] == "Optioneel"


def test_status_nodig_om_te_sturen_for_control_only_gaps():
    cl = _base(capability_ok=False, grid_fuse_unset=True)
    by_key = {i.key: i for i in cl.items}
    assert by_key["capability"].status == "nodig_om_te_sturen"
    assert by_key["grid_fuse"].status == "nodig_om_te_sturen"
    assert by_key["capability"].to_dict()["status"] == "Nodig om te sturen"


def test_mock_price_source_shows_prices_ontbreekt():
    cl = _base(prices_live=False)
    assert next(i for i in cl.items if i.key == "prices").status == "ontbreekt"


def test_p1_and_battery_need_fresh_soc_not_zero_placeholder():
    """#134: without a fresh SoC reading the battery row stays Ontbreekt."""
    cl = _base(soc_ready=False, sensing_ok=False)
    assert next(i for i in cl.items if i.key == "battery").status == "ontbreekt"


def test_p1_ontbreekt_when_grid_stale():
    cl = _base(freshness={"grid": "stale", "soc": "fresh"})
    assert next(i for i in cl.items if i.key == "p1").status == "ontbreekt"


def test_p1_ontbreekt_when_not_paired():
    cl = _base(p1_paired=False)
    assert next(i for i in cl.items if i.key == "p1").status == "ontbreekt"


def _seed_user(db: str, username: str, password: str, role: str):
    s = AuthStore(db)

    async def run():
        await s.init()
        await s.create_user(username, hash_password(password), role)
        await s.close()

    asyncio.run(run())


def test_setup_checklist_endpoint_requires_login(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=AuthStore(db),
    )
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(app) as c:
        assert c.get("/api/setup/checklist").status_code == 401
        login = c.post("/api/auth/login", json={"username": "admin", "password": "pw12345678"})
        tok = login.json()["token"]
        r = c.get("/api/setup/checklist", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        body = r.json()
        assert "items" in body and "headline" in body
        assert body["readiness"]["control_ready"] is False


def test_setup_checklist_wired_app_mock_prices(tmp_path):
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for sig in SIGNALS:
        fr.mark(sig, now)
    ams = ZoneInfo("Europe/Amsterdam")
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        store=HistoryStore(db),
        settings_store=SettingsStore(db),
        freshness=fr,
        price_source=MockPriceSource(ams),
        solar_forecast=MockSolarForecastSource(ams),
        battery=MockBatteryDriver(),
        tz=ams,
    )
    with TestClient(app) as c:
        body = c.get("/api/setup/checklist").json()
    prices = next(i for i in body["items"] if i["key"] == "prices")
    assert prices["status"] == "Ontbreekt"


def _setup_tsx_text() -> str:
    root = __file__.replace("/ems/tests/test_setup_checklist.py", "")
    setup_tsx = f"{root}/ems/web/frontend/src/Setup.tsx"
    return open(setup_tsx, encoding="utf-8").read()


def test_setup_checklist_load_render_does_not_post():
    """Guard (#290/#291): checklist fetch/render never POSTs or toggles control flags."""
    text = _setup_tsx_text()
    assert "control.operational" not in text
    assert "control.dry_run" not in text
    load_end_marker = "}, [fetchGen]);"
    load_end = text.find(load_end_marker)
    assert load_end != -1
    load_start = text.rfind("useEffect(", 0, load_end)
    assert load_start != -1
    load_block = text[load_start : load_end + len(load_end_marker)]
    assert "loadChecklist()" in load_block
    assert 'method: "POST"' not in load_block
    assert "method: 'POST'" not in load_block
    import re

    for block in re.findall(r"useEffect\(\(\) => \{.*?\}, \[[^\]]*\]\);", text, re.DOTALL):
        assert 'method: "POST"' not in block
        assert "method: 'POST'" not in block


def test_setup_wizard_post_targets_are_allowlisted():
    """Guard (#291 AC 14): wizard writes only via setup settings + supervised restart."""
    import re

    text = _setup_tsx_text()
    assert "control.operational" not in text
    assert "control.dry_run" not in text
    post_targets = set(
        re.findall(
            r'apiFetch\(\s*["\'](/api/[^"\']+)["\'][^)]*method:\s*["\']POST["\']',
            text,
        ),
    )
    assert post_targets == {"/api/setup/settings", "/api/system/restart"}
