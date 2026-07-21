"""Iteration 3: the append-only audit log — the store, and the request-driven API hooks
(config change, manual override) surfaced via /api/audit. (The per-cycle battery-decision loop runs
in the lifespan; its dedup is covered here at the store level via last_decision_mode.)"""
import asyncio
import sqlite3
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import aiosqlite
from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.domain import RawSample
from ems.lifecycle import Lifecycle
from ems.planner.schedule import SLOT
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import PriceSlot
from ems.storage.audit import AuditStore
from ems.storage.auth import AuthStore
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


def test_last_decision_mode_does_not_nest_the_self_heal_retry(tmp_path, monkeypatch):
    # F7: last_decision_mode must query through the PRIVATE unwrapped helper, not the wrapped
    # public `recent`. Otherwise a dead connection triggers the retry wrapper on BOTH methods
    # (outer last_decision_mode × inner recent = up to 4 attempts). Count the audit_log SELECTs a
    # persistent dead-connection error provokes: with the fix it is ≤ 2 (one retry total).
    store = AuditStore(str(tmp_path / "ems.sqlite"))
    calls = {"n": 0}
    orig_execute = aiosqlite.Connection.execute

    async def counting_execute(self, sql, *a, **kw):
        if "FROM audit_log" in sql:
            calls["n"] += 1
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        return await orig_execute(self, sql, *a, **kw)

    async def run():
        await store.init()
        monkeypatch.setattr(aiosqlite.Connection, "execute", counting_execute)
        try:
            await store.last_decision_mode()
        except sqlite3.ProgrammingError:
            pass  # persistent dead connection ⇒ the 2nd attempt also propagates — expected

    asyncio.run(run())
    assert calls["n"] <= 2  # one retry at ONE level, never the nested 4 the old code caused


def test_audit_store_between_windows_by_time_oldest_first(tmp_path):
    store = AuditStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.append("2026-06-27T23:00:00+00:00", "battery_decision", "before window", {})
        await store.append("2026-07-06T10:00:00+00:00", "battery_decision", "in window 1", {})
        await store.append("2026-07-08T10:00:00+00:00", "manual_override", "in window 2", {})
        await store.append("2026-07-13T00:00:00+00:00", "battery_decision", "after window", {})
        return await store.between("2026-07-06T00:00:00+00:00", "2026-07-13T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [r["summary"] for r in rows] == ["in window 1", "in window 2"]  # oldest-first, bounded


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


# --- P2 security review: "auth"-category rows (usernames, roles, login events, lockouts, role ---
# --- changes, invites, token mint/revoke) are ADMIN-only; every other category stays reachable ---
# --- by any authenticated role (the Manage -> Audit view is a general transparency surface). -----

def _app_with_auth(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        _Source(), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=_FlatPrices(), solar_forecast=MockSolarForecastSource(AMS),
        controller=ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True),
        settings_store=SettingsStore(db),
        override_store=SettingsStore(db, table="runtime_state"),
        audit_store=AuditStore(db),
        auth_store=AuthStore(db),
    )


def _seed_user(db: str, username: str, password: str, role: str) -> None:
    from ems.authn import hash_password
    s = AuthStore(db)

    async def run():
        await s.init()
        await s.create_user(username, hash_password(password), role)
        await s.close()

    asyncio.run(run())


def _login(c: TestClient, username: str, password: str) -> str:
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_reader_gets_403_on_explicit_auth_category(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    _seed_user(db, "rdr", "pw12345678", "reader")
    with TestClient(_app_with_auth(tmp_path)) as c:
        tok = _login(c, "rdr", "pw12345678")
        h = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/audit", params={"category": "auth"}, headers=h).status_code == 403


def test_reader_sees_non_auth_categories_but_no_auth_rows_leak_through_unfiltered(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    _seed_user(db, "rdr", "pw12345678", "reader")
    with TestClient(_app_with_auth(tmp_path)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        h_admin = {"Authorization": f"Bearer {admin_tok}"}
        # A real manual_override write, so there is a non-"auth" row a reader should still see.
        c.post("/api/override", json={"intent": "grid_charge_to_target", "minutes": 60},
               headers=h_admin)
        rdr_tok = _login(c, "rdr", "pw12345678")  # itself writes a "login_success" auth row
        h_rdr = {"Authorization": f"Bearer {rdr_tok}"}

        unfiltered = c.get("/api/audit", headers=h_rdr).json()["entries"]
        assert any(e["category"] == "manual_override" for e in unfiltered)
        assert all(e["category"] != "auth" for e in unfiltered)

        # The admin, unfiltered AND filtered, still sees the auth-category rows (logins included).
        admin_auth_rows = c.get(
            "/api/audit", params={"category": "auth"}, headers=h_admin).json()["entries"]
        assert any(e["detail"]["event"] == "login_success" for e in admin_auth_rows)
