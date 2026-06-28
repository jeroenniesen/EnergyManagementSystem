"""When the car is charging, the home battery must never discharge into it: the controller's
decision flips any discharging intent to HOLD (idle). Controllable EV reading + flat prices (so the
plan is plain self-consumption), via /api/decision."""
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
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


class _Source:
    """Read-only source with a settable EV power (the rest is benign)."""

    def __init__(self, ev_w: float) -> None:
        self.ev_w = ev_w

    def read(self) -> RawSample:
        return RawSample(grid_power_w=0.0, solar_power_w=0.0, battery_power_w=0.0,
                         ev_power_w=self.ev_w, soc_pct=55.0)


class _FlatPrices:
    """Flat prices -> winter arbitrage finds no trade -> the plan is all self-consumption."""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        self._slots = [PriceSlot(base + i * SLOT, 0.25) for i in range(-2, 96)]

    def slots(self) -> list[PriceSlot]:
        return self._slots


def _app(tmp_path, ev_w: float):
    db = str(tmp_path / "ems.sqlite")
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    return create_app(
        _Source(ev_w), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=_FlatPrices(), solar_forecast=MockSolarForecastSource(AMS),
        controller=controller, settings_store=SettingsStore(db),
    )


def _decision(c):
    # Pin winter so flat prices give a deterministic self-consumption plan.
    c.post("/api/settings", json={"strategy.mode": "winter"})
    return c.get("/api/decision").json()


def test_car_charging_holds_the_battery(tmp_path):
    with TestClient(_app(tmp_path, ev_w=3000.0)) as c:
        b = _decision(c)
    assert b["car_charging"] is True
    assert b["intent"] == "hold_reserve"  # was self-consumption -> held
    assert b["desired_mode"] == "idle"  # IDLE never discharges
    assert "car charging" in b["plan_reason"]


def test_no_hold_when_car_idle(tmp_path):
    with TestClient(_app(tmp_path, ev_w=0.0)) as c:
        b = _decision(c)
    assert b["car_charging"] is False
    assert b["intent"] == "allow_self_consumption"


def test_setting_off_allows_normal_operation_while_charging(tmp_path):
    with TestClient(_app(tmp_path, ev_w=3000.0)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter",
                                      "control.hold_battery_when_car_charging": False})
        b = c.get("/api/decision").json()
    assert b["car_charging"] is True
    assert b["intent"] == "allow_self_consumption"  # not held — the rule is off


def test_below_threshold_is_not_charging(tmp_path):
    with TestClient(_app(tmp_path, ev_w=300.0)) as c:  # below the 500 W threshold
        b = _decision(c)
    assert b["car_charging"] is False
    assert b["intent"] == "allow_self_consumption"
