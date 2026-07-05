"""/api/energy-story: one shape for both directions (past = recorded, next = plan/forecast)."""
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS, Recorder
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import _action_from_battery, _action_from_intent, create_app

AMS = ZoneInfo("Europe/Amsterdam")

_SLOT_KEYS = {"start", "soc_pct", "grid_w", "solar_w", "battery_w", "load_w", "eur_per_kwh",
              "action"}
_TOTAL_KEYS = {"import_kwh", "export_kwh", "solar_kwh", "charge_kwh", "grid_charge_kwh",
               "solar_charge_kwh", "discharge_kwh", "load_kwh", "grid_cost_eur",
               "self_sufficiency_pct", "soc_start_pct", "soc_end_pct", "soc_min_pct", "soc_max_pct"}
_TOP_KEYS = {"window", "now", "current_soc_pct", "reserve_soc_pct", "target_soc_pct", "target_kwh",
             "target_deadline", "current_price_eur_per_kwh", "slots", "totals", "headline"}


def _app(tmp_path, *, with_recorder=False):
    db = str(tmp_path / "ems.sqlite")
    store = HistoryStore(db)
    recorder = None
    if with_recorder:
        fresh = FreshnessTracker()
        fresh.register(*SIGNALS)
        recorder = Recorder(MockSource(), store, fresh, cycle_seconds=999)
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS, store=store, recorder=recorder,
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
    )


def test_charge_actions_split_solar_from_grid():
    # A charging slot is labelled by its DOMINANT source (matching the Sankey's kWh split): solar
    # when the roof supplied most of the charge, grid when the grid did. Crucially, a sunny slot
    # where the battery fills from solar while the house draws a little grid for its OWN load must
    # read SOLAR, not grid (the mislabel that made the actuals track look like it bought power).
    # Actuals — (battery_w, solar_w, load_w):
    assert _action_from_battery(-1000.0, 0.0, 0.0) == "grid_charge"       # night top-up, no sun
    assert _action_from_battery(-1000.0, 1500.0, 200.0) == "solar_charge"  # solar fills it; house
    #                                                                        draws grid for itself
    assert _action_from_battery(-1000.0, 300.0, 200.0) == "grid_charge"   # only 100 W solar surplus
    assert _action_from_battery(-1000.0, 3000.0, 200.0) == "solar_charge"  # plenty of solar
    assert _action_from_battery(1000.0, 0.0, 800.0) == "discharge"
    assert _action_from_battery(0.0, 0.0, 0.0) == "idle"
    # Plan (derived from intent + projected battery power):
    assert _action_from_intent("grid_charge_to_target", battery_w=-1000.0) == "grid_charge"
    assert _action_from_intent("allow_self_consumption", battery_w=-1000.0) == "solar_charge"
    assert _action_from_intent("allow_self_consumption", battery_w=300.0) == "self_consume"
    assert _action_from_intent("discharge_for_load", battery_w=1000.0) == "discharge"


def test_totals_split_charge_into_grid_and_solar(tmp_path):
    # The kWh totals carry the same split, and grid+solar charge sum to the overall charge total.
    with TestClient(_app(tmp_path)) as c:
        t = c.get("/api/energy-story?window=next").json()["totals"]
    assert {"grid_charge_kwh", "solar_charge_kwh"} <= set(t)
    # The two sources sum to (at most) the overall charge total.
    split = round(t["grid_charge_kwh"] + t["solar_charge_kwh"], 2)
    assert split <= round(t["charge_kwh"], 2) + 0.01


def test_next_story_has_the_unified_shape_and_a_headline(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/energy-story?window=next").json()
    assert b["window"] == "next"
    assert set(b) >= _TOP_KEYS
    assert len(b["slots"]) > 0
    assert _SLOT_KEYS <= set(b["slots"][0])
    assert b["slots"][0]["action"] in {
        "grid_charge", "solar_charge", "discharge", "hold", "self_consume", "idle"}
    assert _TOTAL_KEYS <= set(b["totals"])
    assert isinstance(b["headline"], str) and "Next 24h" in b["headline"]
    assert b["target_soc_pct"] is not None


def test_next_defaults_when_window_omitted(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/energy-story").json()["window"] == "next"


def test_next_story_carries_recent_actuals_and_on_track(tmp_path):
    # "Am I on track?" — the next story now also reports the recent-actuals window + a verdict.
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/energy-story?window=next").json()
    assert b["recent_hours"] == 3
    assert isinstance(b["recent"], list)  # no history yet → empty, graceful
    ot = b["on_track"]
    assert ot["status"] in {"ahead", "on_track", "behind", "unknown"}
    assert ot["target_soc_pct"] is not None and "actual_soc_pct" in ot
    assert isinstance(ot["message"], str) and ot["message"]
    assert b["recent_review"] is None  # no history yet → no review (graceful)


def test_verdict_and_headline_never_claim_a_phantom_grid_top_up(tmp_path):
    # The bug we fixed: the verdict said "EMS tops up … from the grid" and the headline promised a
    # top-up, but the plan contained NO grid-charge slot (solar charging was being miscounted as a
    # grid top-up). Invariant: "top up"/"tops up" language may appear ONLY when the plan actually
    # has a grid-charge slot (action == "grid_charge"). Otherwise it's a lie about the plan.
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/energy-story?window=next").json()
    has_grid_charge = any(s["action"] == "grid_charge" for s in b["slots"])
    claims_top_up = "top up" in b["on_track"]["message"].lower() \
        or "tops up" in b["on_track"]["message"].lower() \
        or "top up" in b["headline"].lower()
    if not has_grid_charge:
        assert not claims_top_up, (
            "verdict/headline claims a grid top-up the plan does not contain: "
            f"{b['on_track']['message']!r} / {b['headline']!r}"
        )


def test_recent_actuals_appear_once_history_exists(tmp_path):
    # With a recorded sample, the recent segment carries actuals in the same slot shape as the plan,
    # and the "did we do right" review (solar vs forecast + battery in/out) is populated.
    with TestClient(_app(tmp_path, with_recorder=True)) as c:
        b = c.get("/api/energy-story?window=next").json()
    assert isinstance(b["recent"], list)
    if b["recent"]:
        assert _SLOT_KEYS <= set(b["recent"][0])
        assert b["recent"][0]["action"] in {"charge", "discharge", "idle"}
        rv = b["recent_review"]
        assert rv is not None and isinstance(rv["message"], str) and rv["message"]
        assert "solar_actual_kwh" in rv and "battery_charged_kwh" in rv


def test_past_story_same_shape_built_from_history(tmp_path):
    # The lifespan recorder writes one sample -> the past window has at least one slot.
    with TestClient(_app(tmp_path, with_recorder=True)) as c:
        b = c.get("/api/energy-story?window=past").json()
    assert b["window"] == "past"
    assert set(b) >= _TOP_KEYS
    assert _TOTAL_KEYS <= set(b["totals"])
    assert "Last 24h" in b["headline"] or "No history" in b["headline"]
    if b["slots"]:
        assert _SLOT_KEYS <= set(b["slots"][0])
        assert b["slots"][0]["action"] in {"grid_charge", "solar_charge", "discharge", "idle"}


def test_past_story_empty_without_history(tmp_path):
    # No recorder -> no samples -> graceful empty story (not a crash).
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/energy-story?window=past").json()
    assert b["window"] == "past"
    assert b["slots"] == []
    assert "No history" in b["headline"]


def test_past_actuals_dont_mislabel_solar_charge_as_grid_during_car_charging(tmp_path):
    # Regression (owner report): the past track showed "charged by the grid" while the CAR was
    # charging. The car's grid import inflated house_load, so the battery's own solar-fed charge
    # was mislabelled a grid charge. Balanced slot: grid 1600 + solar 3500 = house 300 + car 4000
    # + battery charge 800 → the 800 W charge came entirely from the 3200 W of solar after the
    # house, so it MUST read solar_charge.
    import asyncio
    from datetime import UTC, datetime, timedelta

    from ems.domain import RawSample
    from ems.load_model import reconstruct

    db = str(tmp_path / "ems.sqlite")

    async def seed():
        st = HistoryStore(db)
        await st.init()
        t = datetime.now(UTC) - timedelta(minutes=30)  # within the last-24h window
        raw = RawSample(grid_power_w=1600.0, solar_power_w=3500.0, battery_power_w=-800.0,
                        ev_power_w=4000.0, soc_pct=55.0)
        await st.record(t.isoformat(), raw, reconstruct(raw))

    asyncio.run(seed())
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
                     store=HistoryStore(db), price_source=MockPriceSource(AMS),
                     solar_forecast=MockSolarForecastSource(AMS), settings_store=SettingsStore(db))
    with TestClient(app) as c:
        b = c.get("/api/energy-story?window=past").json()
    charging = [s for s in b["slots"] if s["battery_w"] < -50]
    assert charging, "expected a charging slot in the seeded history"
    assert all(s["action"] == "solar_charge" for s in charging), \
        f"car-charging slot mislabelled: {[s['action'] for s in charging]}"


def test_invalid_window_is_rejected(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/energy-story?window=sideways").status_code == 422


def test_past_story_prices_come_from_stored_history(tmp_path):
    # Prices are persisted each cycle; the past story must read them so yesterday's price bars
    # aren't blank once the LIVE feed has dropped that (old) slot. Stored 0.11 is a value the mock
    # feed won't produce, so seeing it proves the stored history is used.
    import asyncio
    from datetime import UTC, datetime, timedelta

    from ems.domain import RawSample
    from ems.load_model import reconstruct

    db = str(tmp_path / "ems.sqlite")
    old = (datetime.now(UTC) - timedelta(hours=20)).replace(minute=0, second=0, microsecond=0)

    async def seed():
        st = HistoryStore(db)
        await st.init()
        raw = RawSample(grid_power_w=2000.0, solar_power_w=0.0, battery_power_w=0.0,
                        ev_power_w=0.0, soc_pct=50.0)
        await st.record(old.isoformat(), raw, reconstruct(raw))
        await st.upsert_price_slots([(old.isoformat(), 0.11)])

    asyncio.run(seed())
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
                     store=HistoryStore(db), price_source=MockPriceSource(AMS),
                     solar_forecast=MockSolarForecastSource(AMS), settings_store=SettingsStore(db))
    with TestClient(app) as c:
        b = c.get("/api/energy-story?window=past").json()
    assert any(s["eur_per_kwh"] is not None and abs(s["eur_per_kwh"] - 0.11) < 1e-9
               for s in b["slots"]), "stored historical price did not reach the past story"
