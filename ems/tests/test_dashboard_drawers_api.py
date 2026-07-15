"""Backend contract for the contextual dashboard drawers (2026-07-15 plan): the savings object
distinguishes estimated from realized (never fabricated) and reports complete-day evidence, and
the decisions timeline maps audit rows to homeowner events — including an economic-skip decision
recorded on a no-trade day."""
import asyncio
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.planner.schedule import SLOT
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource, PriceSlot
from ems.storage.audit import AuditStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _fresh():
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for s in SIGNALS:
        fr.mark(s, now)
    return fr


class _FlatPrices:
    def __init__(self):
        now = datetime.now(UTC)
        base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        self._slots = [PriceSlot(base + i * SLOT, 0.25) for i in range(-2, 96)]

    def slots(self):
        return self._slots


def _app(tmp_path, **kw):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        store=HistoryStore(db), audit_store=AuditStore(db), **kw,
    )


def _seed_audit(tmp_path, rows):
    async def run():
        st = AuditStore(str(tmp_path / "ems.sqlite"))
        await st.init()
        for ts, cat, summary, detail in rows:
            await st.append(ts, cat, summary, detail)
        await st.close()

    asyncio.run(run())


def test_savings_contract_distinguishes_estimate_from_realized(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/savings").json()
    # The full contract is present…
    for key in ("estimate_eur", "realized_today_eur", "month_realized_eur", "complete_days",
                "lower_bound_eur", "upper_bound_eur", "today_eur"):
        assert key in b, f"missing savings field {key}"
    # …and on a fresh DB there are no completed days, so realized is NOT fabricated.
    assert b["complete_days"] == 0
    assert b["month_realized_eur"] is None
    # The estimate band is ordered and labelled-estimate (lower <= upper) when an estimate exists.
    if b["estimate_eur"] is not None:
        assert b["lower_bound_eur"] <= b["upper_bound_eur"]


def test_decisions_endpoint_maps_audit_rows(tmp_path):
    app = _app(tmp_path)
    now = datetime.now(UTC).isoformat()
    _seed_audit(tmp_path, [
        (now, "battery_decision", "sent",
         {"outcome": "applied", "desired_mode": "charge", "reason": "cheap window"}),
        (now, "battery_decision", "skip",
         {"outcome": "economic_skip", "reason": "no-trade: spread below break-even"}),
        (now, "settings_change", "cfg", {"keys": ["strategy.mode"]}),
    ])
    with TestClient(app) as c:
        events = c.get("/api/decisions").json()["events"]
    assert len(events) == 2  # the settings_change row is filtered out
    titles = " ".join(e["title"].lower() for e in events)
    assert "charg" in titles and "skip" in titles
    assert all(e["action"] and e["consequence"] is not None for e in events)


def test_no_trade_day_records_an_economic_skip_decision(tmp_path):
    # Operational + flat prices → winter no-trade. The steady-state AUTO cycle records ONE
    # economic-skip decision, surfaced on the decisions timeline.
    db = str(tmp_path / "ems.sqlite")
    ctl = ModeController(MockBatteryDriver(), Lifecycle(dry_run=False, startup_grace_seconds=0),
                         dry_run=False)

    async def seed():
        st = SettingsStore(db)
        await st.init()
        await st.set_many({"strategy.mode": "winter"})
        await st.close()

    asyncio.run(seed())
    app = create_app(
        MockSource(), dry_run=False, dev_mode="live", tz=AMS,
        price_source=_FlatPrices(), solar_forecast=MockSolarForecastSource(AMS),
        controller=ctl, freshness=_fresh(), settings_store=SettingsStore(db),
        audit_store=AuditStore(db), store=HistoryStore(db), control_cycle_seconds=0.02,
    )
    with TestClient(app) as c:
        deadline = time.time() + 3.0
        skips: list = []
        while time.time() < deadline and not skips:
            time.sleep(0.05)
            skips = [e for e in c.get("/api/audit").json()["entries"]
                     if e["detail"].get("outcome") == "economic_skip"]
        assert skips, "a no-trade day must record an economic-skip decision"
        events = c.get("/api/decisions").json()["events"]
    assert any("skip" in e["title"].lower() for e in events)
