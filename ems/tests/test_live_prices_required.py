"""#126 — live control only with live Tibber prices: wiring gate, data-quality, status copy."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.alerts import derive_alerts, prices_ok_for_quality
from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource, PriceSlot
from ems.storage.settings import SettingsStore
from ems.tests.test_control_service import _controlling_controller, _service
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")
ALL_FRESH = {"grid": "fresh", "solar": "fresh", "ev": "fresh", "battery": "fresh", "soc": "fresh"}
SINCE = datetime(2026, 9, 27, 14, 5, tzinfo=UTC)


class _UnavailableTibber:
    """Stand-in for a wired TibberPriceSource in outage (has unavailable_since)."""

    def __init__(self, since: datetime = SINCE, slots: list[PriceSlot] | None = None):
        self._since = since
        self._slots = slots or []

    def slots(self) -> list[PriceSlot]:
        return self._slots

    def unavailable_since(self) -> datetime | None:
        return self._since


def _fresh_tracker():
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for s in SIGNALS:
        fr.mark(s, now)
    return fr


# --- Klaar-als 1–2: wiring (also covered in test_connection.py) ---------------------------------


def test_prices_ok_for_quality_blocks_operational_mock():
    """#126 klaar-als 3 helper: operational + mock → not OK for `complete`."""
    mock = MockPriceSource(AMS)
    assert prices_ok_for_quality(mock, operational_requested=False) is True
    assert prices_ok_for_quality(mock, operational_requested=True) is False
    assert prices_ok_for_quality(None, operational_requested=False) is False
    assert prices_ok_for_quality(_UnavailableTibber(), operational_requested=True) is False


def test_data_quality_not_complete_when_operational_with_mock_prices(tmp_path):
    """#126 klaar-als 3: `_data_quality` /alerts must not report complete for that case."""
    db = str(tmp_path / "ems.sqlite")
    store = SettingsStore(db)
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS),
        freshness=_fresh_tracker(),
        settings_store=store,
    )
    with TestClient(app) as c:
        # operational is restart-tagged but updates settings_cache immediately — enough for DQ.
        c.post("/api/settings", json={"control.operational": True})
        body = c.get("/api/alerts").json()
    assert body["data_quality"] != "complete"
    assert body["data_quality"] == "price_fallback"


# --- Klaar-als 4–5: status badge cases a / b / b-kijkmodus --------------------------------------


def test_case_a_kijkmodus_no_live_prices_critical():
    """#126 (a): startup without live prices while operational → kijkmodus + dry_run True."""
    alerts = derive_alerts(
        ALL_FRESH, dry_run=True, decision_outcome="dry_run",
        mock_prices_blocked_operational=True,
    )
    a = next(x for x in alerts if x.key == "no_live_prices")
    assert a.severity == "critical"
    assert a.message == (
        "Kijkmodus: geen actuele prijzen van Tibber, EMS stuurt de batterij niet."
    )
    # More specific than the generic dry_run info banner.
    assert not any(x.key == "dry_run_active" for x in alerts)


def test_case_b_live_tibber_outage_commands_self_use_and_copy():
    """#126 (b): Tibber down while live → stays live, forces self-use, text b (not a)."""
    # Empty slots so current_plan short-circuits; unavailable_since drives the #126 failsafe
    # (and proves we don't depend on the incomplete-horizon path alone).
    controller = _controlling_controller()
    controller.driver.apply(PhysicalMode.CHARGE)
    svc, _ctx = _service(
        controller, price_source=_UnavailableTibber(slots=[]),
        current_mode=lambda now: controller.driver.current_mode(),
    )
    assert svc._dry_run is False  # stays live — not forced into watch mode
    intent, reason, *_ = svc.effective_intent(datetime.now(UTC))
    records = svc.control_tick(datetime.now(UTC))

    assert intent is BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert "unavailable" in reason
    assert controller.driver.current_mode() is PhysicalMode.AUTO
    assert records  # wrote / acted — not dry-run

    alerts = derive_alerts(
        ALL_FRESH, dry_run=False, decision_outcome=None,
        confirmed_auto=True,
        tibber_unavailable_since=SINCE, site_tz=AMS,
    )
    a = next(x for x in alerts if x.key == "tibber_prices_unavailable")
    hhmm = SINCE.astimezone(AMS).strftime("%H:%M")
    assert f"Geen actuele prijzen van Tibber sinds {hhmm}" in a.message
    assert "EMS heeft de batterij op eigen zelfverbruik gezet" in a.message
    assert "Kijkmodus:" not in a.message
    assert a.severity == "critical"


def test_case_b_unconfirmed_auto_no_mode_name():
    """#126 (b) unconfirmed AUTO: 'Laatst bekende stand: onbekend', no mode name."""
    alerts = derive_alerts(
        ALL_FRESH, dry_run=False, decision_outcome=None,
        confirmed_auto=False,
        tibber_unavailable_since=SINCE, site_tz=AMS,
    )
    a = next(x for x in alerts if x.key == "tibber_prices_unavailable")
    blob = f"{a.message} {a.safe} {a.ems_doing}"
    assert "Laatst bekende stand: onbekend" in a.message
    for mode_name in ("zelfverbruik", "self-use", "self-consumption", "AUTO", "auto"):
        assert mode_name not in blob, f"mode name {mode_name!r} leaked: {blob}"


def test_case_b_kijkmodus_tibber_outage_no_self_use_claim():
    """#126 (b-kijkmodus): dry-run Tibber outage must not claim a self-use battery write."""
    alerts = derive_alerts(
        ALL_FRESH, dry_run=True, decision_outcome="dry_run",
        tibber_unavailable_since=SINCE, site_tz=AMS,
    )
    a = next(x for x in alerts if x.key == "tibber_prices_unavailable")
    assert "EMS kijkt alleen mee en verandert niets aan je batterij" in a.message
    assert "EMS heeft de batterij op eigen zelfverbruik gezet" not in a.message
    assert "Kijkmodus:" not in a.message


def test_alerts_api_case_a_surfaces_kijkmodus(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS),
        freshness=_fresh_tracker(),
        settings_store=SettingsStore(db),
        controller=ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True),
    )
    with TestClient(app) as c:
        c.post("/api/settings", json={"control.operational": True})
        alerts = c.get("/api/alerts").json()["alerts"]
    a = next(x for x in alerts if x["key"] == "no_live_prices")
    assert a["severity"] == "critical"
    assert "Kijkmodus: geen actuele prijzen van Tibber" in a["message"]
