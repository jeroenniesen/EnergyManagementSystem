"""Advisory-only "best time to charge the car" (docs/v2-ev-control.md: EV control is out of scope
for v2 — this never commands anything). Pure logic in `ems/ev_advisor.py` + the read-only
`/api/advisor/ev-charge` endpoint that wires it to settings + the existing price/forecast access
patterns."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.ev_advisor import advise_charge_window
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource, PriceSlot
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")
BASE = datetime(2026, 7, 11, 0, 0, tzinfo=UTC)


def _slots(prices: list[float], base: datetime = BASE) -> list[PriceSlot]:
    return [PriceSlot(base + i * timedelta(minutes=15), p) for i, p in enumerate(prices)]


# ---- pure ems/ev_advisor.advise_charge_window ----

def test_cheapest_contiguous_window_beats_the_cheapest_lone_slot():
    # 8 slots (2h), duration needed = ceil((1.0/2.0 kWh/kW)/0.25) = 2 slots.
    # Slot 0 alone is the cheapest (0.05) but its neighbour (0.50) makes window [0,1] cost
    # (0.05+0.50)*0.5 = 0.275; window [2,3] (0.20+0.20)*0.5 = 0.20 is the true cheapest — the
    # search must consider CONTIGUOUS pairs, not just the single cheapest slot.
    prices = [0.05, 0.50, 0.20, 0.20, 0.50, 0.50, 0.50, 0.50]
    advice = advise_charge_window(
        _slots(prices), {}, departure=BASE + timedelta(hours=2),
        kwh_needed=1.0, charger_kw=2.0, now=BASE,
    )
    assert advice is not None
    assert advice["slots"] == 2
    assert advice["start"] == (BASE + timedelta(minutes=30)).isoformat()
    assert advice["end"] == (BASE + timedelta(minutes=60)).isoformat()
    assert advice["est_cost_eur"] == 0.20  # (0.20+0.20) €/kWh * 0.5 kWh/slot
    assert advice["solar_share_pct"] == 0
    assert "window before your" in advice["reason"]


def test_surplus_discount_shifts_the_window_under_spot_minus_tax_not_net_metering():
    # 80 slots (00:00-20:00). Slot 0 (00:00): price 0.10, no solar. Slot 48 (12:00): price 0.15,
    # p50=2000W (surplus, >= the 1000W default threshold). Everything else 0.50, no solar.
    # duration needed = ceil((0.5/4.0)/0.25) = 1 slot; kwh_per_slot = 0.5 kWh.
    prices = [0.50] * 80
    prices[0] = 0.10
    prices[48] = 0.15
    slots = _slots(prices)
    p50 = {slots[48].start: 2000.0}
    departure = BASE + timedelta(hours=20)

    # net_metering (today's saldering): export_value == price regardless of surplus, so this is
    # pure price arbitrage — slot 0 (0.10) beats slot 48 (0.15) even though 48 has solar surplus.
    net = advise_charge_window(
        slots, p50, departure=departure, kwh_needed=0.5, charger_kw=4.0, now=BASE,
        export_model="net_metering",
    )
    assert net is not None
    assert net["start"] == slots[0].start.isoformat()
    assert net["est_cost_eur"] == 0.05  # 0.10 €/kWh * 0.5 kWh
    assert net["solar_share_pct"] == 0

    # spot_minus_tax (post-2027): slot 48's surplus is valued at export_value = 0.15 - 0.13 = 0.02,
    # cheaper than slot 0's full 0.10 — the recommended window SHIFTS to midday, the key
    # post-2027 behaviour change.
    dyn = advise_charge_window(
        slots, p50, departure=departure, kwh_needed=0.5, charger_kw=4.0, now=BASE,
        export_model="spot_minus_tax", energy_tax_eur_per_kwh=0.13,
    )
    assert dyn is not None
    assert dyn["start"] == slots[48].start.isoformat()
    assert dyn["est_cost_eur"] == 0.01  # 0.02 €/kWh * 0.5 kWh
    assert dyn["solar_share_pct"] == 100


def test_not_enough_slots_returns_none():
    # Only 1 slot available, but 2 are needed (ceil((1.0/2.0)/0.25) = 2).
    advice = advise_charge_window(
        _slots([0.10]), {}, departure=BASE + timedelta(hours=2),
        kwh_needed=1.0, charger_kw=2.0, now=BASE,
    )
    assert advice is None


def test_departure_already_passed_returns_none():
    slots = _slots([0.10] * 8)
    advice = advise_charge_window(
        slots, {}, departure=BASE + timedelta(minutes=30),
        kwh_needed=1.0, charger_kw=2.0, now=BASE + timedelta(hours=1),
    )
    assert advice is None


def test_zero_or_negative_need_returns_none():
    slots = _slots([0.10] * 8)
    assert advise_charge_window(slots, {}, departure=BASE + timedelta(hours=2),
                                kwh_needed=0.0, charger_kw=4.0, now=BASE) is None
    assert advise_charge_window(slots, {}, departure=BASE + timedelta(hours=2),
                                kwh_needed=5.0, charger_kw=0.0, now=BASE) is None


def test_tie_picks_the_earliest_window():
    # 4 identical-price slots, duration=2 → windows [0,1], [1,2], [2,3] all cost the same;
    # the earliest (starting at slot 0) must win.
    slots = _slots([0.20, 0.20, 0.20, 0.20])
    advice = advise_charge_window(
        slots, {}, departure=BASE + timedelta(hours=1),
        kwh_needed=1.0, charger_kw=2.0, now=BASE,
    )
    assert advice is not None
    assert advice["start"] == slots[0].start.isoformat()


def test_solar_share_pct_is_the_percentage_of_surplus_slots_in_the_window():
    # duration = ceil((4.0/4.0)/0.25) = 4 slots — exactly the 4 available, one window only.
    # 3 of the 4 have solar surplus (p50 >= 1000W) → 3/4 = 75%.
    slots = _slots([0.20, 0.20, 0.20, 0.20])
    p50 = {slots[0].start: 2000.0, slots[1].start: 2000.0,
           slots[2].start: 2000.0, slots[3].start: 0.0}
    advice = advise_charge_window(
        slots, p50, departure=BASE + timedelta(hours=1),
        kwh_needed=4.0, charger_kw=4.0, now=BASE,
    )
    assert advice is not None
    assert advice["slots"] == 4
    assert advice["solar_share_pct"] == 75


# ---- GET /api/advisor/ev-charge ----

def _app(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
    )


def test_ev_advisor_endpoint_null_when_disabled(tmp_path):
    with TestClient(_app(tmp_path)) as c:  # ev.advice_enabled defaults False
        body = c.get("/api/advisor/ev-charge").json()
    assert body == {"advice": None}


def test_ev_advisor_endpoint_returns_advice_shape_when_enabled(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={
            "ev.advice_enabled": True, "ev.departure_time": "07:30",
            "ev.charge_kwh": 20.0, "ev.charger_kw": 11.0,
        })
        body = c.get("/api/advisor/ev-charge").json()
    advice = body["advice"]
    assert advice is not None
    for key in ("start", "end", "est_cost_eur", "solar_share_pct", "slots", "reason"):
        assert key in advice
    assert advice["slots"] > 0
    assert "07:30" in advice["reason"]
