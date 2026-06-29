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
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")

_SLOT_KEYS = {"start", "soc_pct", "grid_w", "solar_w", "battery_w", "load_w", "eur_per_kwh",
              "action"}
_TOTAL_KEYS = {"import_kwh", "export_kwh", "solar_kwh", "charge_kwh", "discharge_kwh", "load_kwh",
               "grid_cost_eur", "self_sufficiency_pct", "soc_start_pct", "soc_end_pct",
               "soc_min_pct", "soc_max_pct"}
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


def test_next_story_has_the_unified_shape_and_a_headline(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/energy-story?window=next").json()
    assert b["window"] == "next"
    assert set(b) >= _TOP_KEYS
    assert len(b["slots"]) > 0
    assert _SLOT_KEYS <= set(b["slots"][0])
    assert b["slots"][0]["action"] in {"charge", "discharge", "hold", "self_consume", "idle"}
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


def test_recent_actuals_appear_once_history_exists(tmp_path):
    # With a recorded sample, the recent segment carries actuals in the same slot shape as the plan.
    with TestClient(_app(tmp_path, with_recorder=True)) as c:
        b = c.get("/api/energy-story?window=next").json()
    assert isinstance(b["recent"], list)
    if b["recent"]:
        assert _SLOT_KEYS <= set(b["recent"][0])
        assert b["recent"][0]["action"] in {"charge", "discharge", "idle"}


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
        assert b["slots"][0]["action"] in {"charge", "discharge", "idle"}


def test_past_story_empty_without_history(tmp_path):
    # No recorder -> no samples -> graceful empty story (not a crash).
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/energy-story?window=past").json()
    assert b["window"] == "past"
    assert b["slots"] == []
    assert "No history" in b["headline"]


def test_invalid_window_is_rejected(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/energy-story?window=sideways").status_code == 422
