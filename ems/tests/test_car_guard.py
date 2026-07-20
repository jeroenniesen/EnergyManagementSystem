"""When the car is charging, the home battery must never discharge into it: the controller's
decision flips any discharging intent to HOLD (idle). Controllable EV reading + flat prices (so the
plan is plain self-consumption), via /api/decision."""
import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.domain import RawSample
from ems.ev_schedule import default_schedule
from ems.lifecycle import Lifecycle
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import MockPriceSource, PriceSlot
from ems.storage.history import HistoryStore
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
        self._slots = [PriceSlot(slot.start, 0.25) for slot in MockPriceSource(AMS).slots()]

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


# ---- Regression pin (design 2026-07-12): the EV charging-advice feature introduces NO path
# ---- around this guard — it is advisory-only and never touches the battery decision. ----

def _all_days_enabled(min_pct: int = 80, ready_by: str = "07:30") -> dict:
    sched = default_schedule()
    for day in sched:
        sched[day] = {"enabled": True, "min_pct": min_pct, "ready_by": ready_by}
    return sched


def _app_with_ev_plan(tmp_path, ev_w: float):
    """Same wiring as `_app`, plus a `HistoryStore` so `/api/car/plan` (the EV feature's own read)
    is reachable — proving the two features are independently wired, not just independently
    tested."""
    db = str(tmp_path / "ems.sqlite")
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    return create_app(
        _Source(ev_w), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db),
        price_source=_FlatPrices(), solar_forecast=MockSolarForecastSource(AMS),
        controller=controller, settings_store=SettingsStore(db),
    )


def test_car_guard_holds_even_while_an_ev_charging_window_is_active(tmp_path):
    """The goal's hard requirement: 'if a setting is set to put the home battery in standby when
    charging the car, that still applies' — even while the EV plan's OWN schedule says the car
    should be charging right now. `_car_guard` reads only live `ev_power_w` + the setting; it has
    no dependency on `ems.ev_planner`/`ev_schedule`/`ev_session`, so wiring the EV advice feature in
    cannot open a path around it. This test proves that by driving both surfaces at once: the EV
    plan (anchor far below its scheduled minimum, so its own greedy planner schedules an imminent
    charging window) and the battery decision (the car physically drawing power above threshold)."""
    with TestClient(_app_with_ev_plan(tmp_path, ev_w=3000.0)) as c:
        # Configure the EV feature so its OWN plan wants to charge immediately: anchor far below
        # the minimum with every day enabled -> a large deficit -> under flat (tied) prices the
        # cheapest slots are the earliest ones, i.e. the very next slot after `now`.
        c.post("/api/settings", json={
            "ev.advice_enabled": True,
            "ev.schedule": json.dumps(_all_days_enabled(min_pct=80)),
        })
        c.post("/api/car/soc", json={"pct": 20})
        ev_plan = c.get("/api/car/plan").json()

        # Sanity: the EV plan really does show an imminent charging window — this is what makes
        # the test a genuine regression pin rather than a restatement of test_car_guard_holds_the_
        # battery (a wiring bug that let the EV plan influence the battery would only be caught
        # while a window is actually active).
        assert ev_plan["enabled"] is True
        windows = ev_plan["plan"]["windows"]
        assert windows, "expected the EV plan to schedule an imminent charging window"
        first_start = datetime.fromisoformat(windows[0]["start"])
        assert first_start - datetime.now(UTC) < timedelta(minutes=15), (
            "expected the earliest EV charging slot to be the very next one after `now`"
        )

        # The battery decision is untouched by any of this — the car-guard alone decides, exactly
        # as when the EV feature doesn't exist at all.
        b = _decision(c)
    assert b["car_charging"] is True
    assert b["intent"] == "hold_reserve"  # still held, EV plan or no EV plan
    assert b["desired_mode"] == "idle"
    assert "car charging" in b["plan_reason"]


_EV_ADVISORY_MODULES = ("ems/ev_planner.py", "ems/ev_schedule.py", "ems/ev_session.py")


def test_ev_advisory_modules_never_import_the_battery_or_control_layer():
    """Cheap, honest static check standing in for a full call-graph audit: the design doc states
    `ev_planner.py` "NEVER commands anything" (advisory/visual only in v1). Verified here by
    parsing each module's own `import`/`from ... import` statements (not a textual grep — a
    docstring that merely *mentions* "battery" must not fail this) and asserting none of them
    reaches `ems.sources.battery` (the single battery writer, per CLAUDE.md) or `ems.control`
    (the mode controller). If a future change wires one of these modules into a write path, this
    test breaks immediately."""
    repo_root = Path(__file__).resolve().parents[2]
    for rel in _EV_ADVISORY_MODULES:
        tree = ast.parse((repo_root / rel).read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {m for m in imported if "battery" in m or m.startswith("ems.control")}
        assert not forbidden, f"{rel} must not import the battery/control layer: {forbidden}"
