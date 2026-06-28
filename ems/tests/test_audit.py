"""Iteration 3: the append-only audit log — the store, and the request-driven API hooks
(config change, manual override) surfaced via /api/audit. (The per-cycle battery-decision loop runs
in the lifespan; its dedup is covered here at the store level via last_decision_mode.)"""
import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.domain import RawSample
from ems.lifecycle import Lifecycle
from ems.planner.schedule import SLOT
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import PriceSlot
from ems.storage.audit import AuditStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def test_audit_store_append_recent_filter_and_last_mode(tmp_path):
    store = AuditStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.append("2026-06-28T10:00:00+00:00", "config_change", "changed x",
                           {"keys": ["x"]})
        await store.append("2026-06-28T10:05:00+00:00", "battery_decision", "set charge",
                           {"desired_mode": "charge"})
        await store.append("2026-06-28T10:10:00+00:00", "battery_decision", "set auto",
                           {"desired_mode": "auto"})
        return (await store.recent(10), await store.recent(10, "battery_decision"),
                await store.last_decision_mode())

    allr, decs, last = asyncio.run(run())
    assert len(allr) == 3 and allr[0]["summary"] == "set auto"      # newest-first
    assert allr[0]["detail"]["desired_mode"] == "auto"             # detail decoded to a dict
    assert len(decs) == 2 and all(e["category"] == "battery_decision" for e in decs)
    assert last == "auto"                                          # dedup seed = latest mode


class _Source:
    def read(self) -> RawSample:
        return RawSample(grid_power_w=0.0, solar_power_w=0.0, battery_power_w=0.0,
                         ev_power_w=0.0, soc_pct=55.0)


class _FlatPrices:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        self._slots = [PriceSlot(base + i * SLOT, 0.25) for i in range(-2, 96)]

    def slots(self) -> list[PriceSlot]:
        return self._slots


def _app(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        _Source(), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=_FlatPrices(), solar_forecast=MockSolarForecastSource(AMS),
        controller=ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True),
        settings_store=SettingsStore(db),
        override_store=SettingsStore(db, table="runtime_state"),
        audit_store=AuditStore(db),
    )


def test_config_change_is_audited(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"battery.min_reserve_soc": 15})
        entries = c.get("/api/audit", params={"category": "config_change"}).json()["entries"]
    assert entries and "battery.min_reserve_soc" in entries[0]["summary"]
    assert "battery.min_reserve_soc" in entries[0]["detail"]["keys"]


def test_secret_value_is_never_written_to_the_audit_log(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"explainer.api_key": "super-secret-123"})
        entries = c.get("/api/audit").json()["entries"]
    assert "super-secret-123" not in str(entries)                  # the VALUE never appears
    assert "explainer.api_key" in entries[0]["detail"]["secrets"]  # only the key name is recorded


def test_manual_override_set_and_clear_are_audited(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/override", json={"intent": "grid_charge_to_target", "minutes": 60})
        c.post("/api/override", json={"intent": "none"})  # clear
        entries = c.get("/api/audit", params={"category": "manual_override"}).json()["entries"]
    assert len(entries) == 2
    assert entries[0]["detail"]["action"] == "clear"               # newest-first
    assert entries[1]["detail"]["intent"] == "grid_charge_to_target"
