"""#227 — contiguous start-window advice (pure + API, never starts appliances)."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.consumers.mock import MockConsumerSource
from ems.consumers.models import (
    ConsumerCapabilities,
    ConsumerDevice,
    ConsumerKind,
)
from ems.consumers.selection import MOCK_DEVICE_IDS_KEY
from ems.consumers.start_advice import propose_start_window
from ems.sources.mock import MockSource
from ems.sources.prices import PriceSlot
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app
from ems.web.routes.consumers import gather_start_advice

BASE = datetime(2026, 7, 11, 0, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)


def _slots(prices: list[float], base: datetime = BASE) -> list[PriceSlot]:
    return [PriceSlot(base + i * SLOT, p) for i, p in enumerate(prices)]


def test_cheapest_contiguous_window_beats_lone_cheap_slot():
    # Same shape as EV advisor: lone 0.05 + expensive neighbour loses to a cheap pair.
    prices = [0.05, 0.50, 0.20, 0.20, 0.50, 0.50, 0.50, 0.50]
    advice = propose_start_window(
        _slots(prices), duration_slots=2, now=BASE, assumed_kwh=1.0,
    )
    assert advice is not None
    assert advice["slots"] == 2
    assert advice["start"] == (BASE + timedelta(minutes=30)).isoformat()
    assert advice["end"] == (BASE + timedelta(minutes=60)).isoformat()
    assert advice["avg_eur_per_kwh"] == 0.20
    assert advice["est_cost_eur"] == 0.20  # (0.20+0.20) * 0.5 kWh/slot
    assert "Cheapest" in advice["reason"]


def test_earliest_window_wins_ties():
    prices = [0.10, 0.10, 0.10, 0.10]
    advice = propose_start_window(
        _slots(prices), duration_slots=2, now=BASE, assumed_kwh=None,
    )
    assert advice is not None
    assert advice["start"] == BASE.isoformat()
    assert "est_cost_eur" not in advice


def test_insufficient_slots_returns_none():
    assert propose_start_window(
        _slots([0.1, 0.2]), duration_slots=4, now=BASE,
    ) is None


def test_horizon_end_clips_candidates():
    prices = [0.50] * 8
    prices[6] = 0.05
    prices[7] = 0.05
    # Horizon ends before the cheap pair → no window of length 2 in range.
    advice = propose_start_window(
        _slots(prices),
        duration_slots=2,
        now=BASE,
        horizon_end=BASE + timedelta(minutes=60),
    )
    assert advice is not None
    assert advice["start"] == BASE.isoformat()  # only early slots available


def test_gather_skips_devices_without_delayed_start():
    caps = ConsumerCapabilities(supports_delayed_start=False)
    src = MockConsumerSource(
        devices=[
            ConsumerDevice(
                id="mock-washer-1",
                name="Washer",
                kind=ConsumerKind.WASHER,
                integration="mock",
                capabilities=caps,
            ),
        ],
        settings={MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]'},
    )
    body = gather_start_advice(
        [src],
        {
            MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]',
            "consumers.dry_run": True,
            "consumers.start_advice_enabled": True,
            "consumers.default_duration_slots": 2,
        },
        _slots([0.1, 0.1, 0.1, 0.1]),
        now=BASE,
    )
    assert body["dry_run"] is True
    assert body["live_armed"] is False
    assert len(body["advice"]) == 1
    assert body["advice"][0]["eligible"] is False
    assert body["advice"][0]["window"] is None
    assert "delayed start" in (body["advice"][0]["skip_reason"] or "").lower()


def test_gather_proposes_for_idle_delayed_start_device():
    src = MockConsumerSource(
        settings={MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]'},
    )
    prices = [0.40, 0.40, 0.10, 0.10, 0.40, 0.40]
    body = gather_start_advice(
        [src],
        {
            MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]',
            "consumers.dry_run": True,
            "consumers.start_advice_enabled": True,
            "consumers.default_duration_slots": 2,
        },
        _slots(prices),
        now=BASE,
    )
    assert body["enabled"] is True
    tip = body["advice"][0]
    assert tip["eligible"] is True
    assert tip["window"] is not None
    assert tip["window"]["start"] == (BASE + timedelta(minutes=30)).isoformat()


def test_api_start_advice_dry_run_default(tmp_path):
    """Route contract: dry-run default, never armed, footnote present (#227)."""
    class _FixedPrices:
        def slots(self):
            # Horizon relative to wall clock so the route can form a window.
            now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
            return [PriceSlot(now + i * SLOT, p) for i, p in enumerate(
                [0.30, 0.05, 0.05, 0.30, 0.30, 0.30, 0.30, 0.30],
            )]

    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(),
        dry_run=True,
        config_dry_run=False,
        dev_mode="mock",
        store=HistoryStore(db),
        settings_store=SettingsStore(db),
        price_source=_FixedPrices(),  # type: ignore[arg-type]
        tz=ZoneInfo("UTC"),
        consumer_sources=[MockConsumerSource()],
    )
    with TestClient(app) as c:
        assert c.post(
            "/api/settings",
            json={MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]'},
        ).status_code == 200
        r = c.get("/api/consumers/start-advice")
        assert r.status_code == 200
        body = r.json()
    assert body["dry_run"] is True
    assert body["live_armed"] is False
    footnote = body["footnote"].lower()
    assert "won’t start" in footnote or "wont start" in footnote
    assert body["enabled"] is True
    assert len(body["advice"]) == 1
    tip = body["advice"][0]
    assert tip["eligible"] is True
    assert tip["window"] is not None
    assert tip["window"]["slots"] == 4


def test_api_start_advice_respects_disabled_flag(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(),
        dry_run=True,
        config_dry_run=False,
        dev_mode="mock",
        store=HistoryStore(db),
        settings_store=SettingsStore(db),
        consumer_sources=[MockConsumerSource()],
    )
    with TestClient(app) as c:
        assert c.post(
            "/api/settings",
            json={
                MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]',
                "consumers.start_advice_enabled": False,
            },
        ).status_code == 200
        body = c.get("/api/consumers/start-advice").json()
    assert body["enabled"] is False
    assert body["advice"] == []


def test_no_start_surface_on_port():
    src = MockConsumerSource()
    assert not hasattr(src, "start")
    assert not hasattr(src, "apply")


def test_settings_defaults_dry_run_true():
    from ems.settings import SETTINGS_BY_KEY, defaults

    assert SETTINGS_BY_KEY["consumers.dry_run"].default is True
    assert defaults()["consumers.dry_run"] is True
    assert defaults()["consumers.start_advice_enabled"] is True
    assert defaults()["consumers.default_duration_slots"] == 4
