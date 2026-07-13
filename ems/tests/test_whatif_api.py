"""Router tests for GET /api/counterfactual (B-69) and POST /api/whatif (B-73) — both ride the
SAME read-only `ems.replay.replay_range` engine `test_replay.py` exercises directly. Seeding here
mirrors `test_replay.py`'s own seed() pattern (HistoryStore.record + upsert_price_slots over a
synthetic winter arbitrage day) rather than importing its underscore-prefixed helpers across test
modules.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import ems.web.routes.whatif as whatif_mod
from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

UTZ = ZoneInfo("UTC")
# Anchored to real "now" (NOT a fixed calendar date, unlike test_replay.py's DB-only tests): these
# tests boot a full app via create_app/TestClient, whose maintenance loop purges history older than
# `history_retention_days` (default 90) on EVERY boot. A fixed past date would eventually (and did,
# non-deterministically, the moment "now" drifted 90+ days past it) race that purge against our own
# seeding. Days-ago-from-now keeps the seeded window forever inside the retention default.
FIRST_DAY = (datetime.now(UTC) - timedelta(days=10)).replace(
    hour=0, minute=0, second=0, microsecond=0)
# strategy.mode is still pinned to "winter" explicitly by every seeding helper below (regardless of
# the real calendar month `FIRST_DAY` lands in) so the resolved strategy stays deterministic.


def _winter_price(i: int) -> float:
    """Cheap night (0-5h) / cheap midday (6-16h) / 0.50 evening peak (17-20h) / cheap late (21-23h)
    — the same shape as test_replay.py's `_winter_price`, an arbitrage-friendly day."""
    h = i // 4
    if h < 6:
        return 0.10
    if 17 <= h < 21:
        return 0.50
    if h >= 21:
        return 0.15
    return 0.20


def _seed_winter_days(db: str, n_days: int, *, start: datetime = FIRST_DAY) -> None:
    """`n_days` consecutive flat-1kW, zero-solar days under `_winter_price` — the planner should
    strictly beat both no_battery and auto_selfuse on every one of them (mirrors
    test_replay.py's test_planner_beats_auto_on_winter_arbitrage_day)."""

    async def go() -> None:
        store = HistoryStore(db)
        await store.init()
        for day_i in range(n_days):
            day = start + timedelta(days=day_i)
            price_rows = []
            for i in range(96):
                ts = (day + timedelta(minutes=15 * i)).isoformat()
                raw = RawSample(grid_power_w=1000.0, solar_power_w=0.0, battery_power_w=0.0,
                                 ev_power_w=0.0, soc_pct=0.0)
                await store.record(ts, raw, reconstruct(raw))
                price_rows.append((ts, _winter_price(i)))
            await store.upsert_price_slots(price_rows)

    asyncio.run(go())


def _seed_settings(db: str, values: dict) -> None:
    async def go() -> None:
        s = SettingsStore(db)
        await s.init()
        await s.set_many(values)

    asyncio.run(go())


def _app(db: str, *, token: str | None = None, with_store: bool = True):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=UTZ,
        store=HistoryStore(db) if with_store else None,
        settings_store=SettingsStore(db),
        web_auth_token=token,
        # Defense in depth alongside the relative FIRST_DAY above: the maintenance loop's retention
        # purge / backup both run unawaited at boot (see _maintenance_loop in api.py) and would
        # otherwise race replay_range's read of the very data we just seeded.
        history_retention_days=0, history_backup_keep=0,
    )


# --------------------------------------------------------------------------------------------------
# B-69 · GET /api/counterfactual
# --------------------------------------------------------------------------------------------------
def test_counterfactual_shape_and_delta_math_consistency(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_winter_days(db, 3)
    _seed_settings(db, {"strategy.mode": "winter"})

    with TestClient(_app(db)) as c:
        body = c.get("/api/counterfactual?days=3").json()

    assert body["days_used"] == 3
    assert body["days_skipped"] == 0
    assert set(body["scenarios"].keys()) == {"no_battery", "auto_selfuse", "planner"}
    for name in ("no_battery", "auto_selfuse", "planner"):
        s = body["scenarios"][name]
        assert set(s.keys()) == {"cost_eur", "import_kwh", "export_kwh"}
        assert s["cost_eur"] is not None

    nb = body["scenarios"]["no_battery"]["cost_eur"]
    auto = body["scenarios"]["auto_selfuse"]["cost_eur"]
    planner = body["scenarios"]["planner"]["cost_eur"]
    # The planner genuinely arbitrages the cheap-night/expensive-evening spread.
    assert planner < auto <= nb + 1e-9

    deltas = body["deltas"]
    assert abs(deltas["planner_vs_no_battery"] - (nb - planner)) < 1e-6
    assert abs(deltas["planner_vs_auto"] - (auto - planner)) < 1e-6

    assert body["window"] is not None
    assert body["window"]["start"] <= body["window"]["end"]
    assert "€" in body["note"] and "3" in body["note"]


def test_counterfactual_without_a_store_is_graceful(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db, with_store=False)) as c:
        body = c.get("/api/counterfactual").json()
    assert body["days_used"] == 0
    assert body["window"] is None
    assert body["deltas"] == {"planner_vs_no_battery": None, "planner_vs_auto": None}
    for s in body["scenarios"].values():
        assert s["cost_eur"] is None


def test_counterfactual_empty_history_is_graceful_not_fabricated(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db)) as c:  # store configured, but nothing recorded yet
        body = c.get("/api/counterfactual?days=5").json()
    assert body["days_used"] == 0
    assert body["scenarios"]["planner"]["cost_eur"] is None
    assert body["deltas"]["planner_vs_no_battery"] is None
    assert "not enough" in body["note"].lower() or "no " in body["note"].lower()


def test_counterfactual_is_cached_in_process_for_15_minutes(tmp_path, monkeypatch):
    db = str(tmp_path / "ems.sqlite")
    _seed_winter_days(db, 2)
    _seed_settings(db, {"strategy.mode": "winter"})

    calls = {"n": 0}
    original = whatif_mod.replay_range

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(whatif_mod, "replay_range", counting)

    with TestClient(_app(db)) as c:
        first = c.get("/api/counterfactual?days=2").json()
        second = c.get("/api/counterfactual?days=2").json()

    assert calls["n"] == 1  # the second call hit the in-process cache, not a second replay
    assert first == second


def test_counterfactual_different_days_param_is_a_separate_cache_key(tmp_path, monkeypatch):
    db = str(tmp_path / "ems.sqlite")
    _seed_winter_days(db, 3)
    _seed_settings(db, {"strategy.mode": "winter"})

    calls = {"n": 0}
    original = whatif_mod.replay_range

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(whatif_mod, "replay_range", counting)

    with TestClient(_app(db)) as c:
        c.get("/api/counterfactual?days=2")
        c.get("/api/counterfactual?days=3")

    assert calls["n"] == 2


# --------------------------------------------------------------------------------------------------
# B-73 · POST /api/whatif
# --------------------------------------------------------------------------------------------------
def test_whatif_rejects_unknown_override_key(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_winter_days(db, 2)
    _seed_settings(db, {"strategy.mode": "winter"})

    with TestClient(_app(db)) as c:
        r = c.post("/api/whatif", json={
            "overrides": {"battery.usable_kwh": 20.0}, "days": 2,
        })
    assert r.status_code == 422
    assert "battery.usable_kwh" in r.json().get("errors", {})


def test_whatif_rejects_invalid_value_for_an_allowed_key(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_winter_days(db, 2)
    _seed_settings(db, {"strategy.mode": "winter"})

    with TestClient(_app(db)) as c:
        r = c.post("/api/whatif", json={
            "overrides": {"battery.min_reserve_soc": 999.0}, "days": 2,
        })
    assert r.status_code == 422
    assert "battery.min_reserve_soc" in r.json().get("errors", {})


def test_whatif_huge_reserve_override_makes_the_variant_deterministically_dearer(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_winter_days(db, 3)
    _seed_settings(db, {"strategy.mode": "winter"})

    with TestClient(_app(db)) as c:
        r = c.post("/api/whatif", json={
            "overrides": {"battery.min_reserve_soc": 50.0}, "days": 3,
        })
    assert r.status_code == 200
    body = r.json()
    assert body["simulation"] is True
    assert body["days_used"] == 3
    assert body["overrides"] == {"battery.min_reserve_soc": 50.0}
    # Each seeded day starts at SoC 0% (below a 50% reserve floor), so the planner must spend the
    # cheap night charging PAST the floor before it can discharge anything into the evening peak —
    # far less effective arbitrage than the 10%-reserve default -> strictly dearer baseline/variant.
    assert body["baseline"]["cost_eur"] is not None
    assert body["variant"]["cost_eur"] is not None
    assert body["variant"]["cost_eur"] > body["baseline"]["cost_eur"]
    assert body["delta_eur"] < 0  # + = variant cheaper; here the variant is dearer
    assert len(body["per_day"]) == 3
    for row in body["per_day"]:
        assert set(row.keys()) == {"date", "baseline_eur", "variant_eur", "delta_eur"}
    assert "€" in body["note"]


def test_whatif_never_writes_settings(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_winter_days(db, 2)
    _seed_settings(db, {"strategy.mode": "winter"})

    with TestClient(_app(db)) as c:
        before = c.get("/api/settings").json()
        r = c.post("/api/whatif", json={
            "overrides": {"planner.negative_price_soak": True}, "days": 2,
        })
        assert r.status_code == 200
        after = c.get("/api/settings").json()
    assert before == after  # a simulation changes nothing


def test_whatif_without_a_store_returns_503(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db, with_store=False)) as c:
        r = c.post("/api/whatif", json={"overrides": {}, "days": 7})
    assert r.status_code == 503


def test_whatif_defaults_days_to_seven(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_winter_days(db, 7)
    _seed_settings(db, {"strategy.mode": "winter"})

    with TestClient(_app(db)) as c:
        r = c.post("/api/whatif", json={"overrides": {"planner.solar_confidence": 60.0}})
    assert r.status_code == 200
    assert r.json()["days"] == 7


def test_whatif_is_not_gated_by_write_auth(tmp_path):
    # Deliberately outside _WRITE_API_PATHS (see api.py + the whatif.py module docstring): a
    # simulation changes nothing, so it stays reachable without a token even when one is set.
    db = str(tmp_path / "ems.sqlite")
    _seed_winter_days(db, 2)
    _seed_settings(db, {"strategy.mode": "winter"})

    with TestClient(_app(db, token="s3cret")) as c:
        r = c.post("/api/whatif", json={"overrides": {}, "days": 2})
    assert r.status_code != 401
