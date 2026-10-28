"""Advisory-only "best time to charge the car" (docs/v2-ev-control.md: EV control is out of scope
for v2 — this never commands anything). Pure logic in `ems/ev_advisor.py` + the read-only
`/api/advisor/ev-charge` endpoint that wires it to settings + the existing price/forecast access
patterns."""
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
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


def test_surplus_export_models_and_fees_match_economic_snapshot():
    slots = _slots([0.20, 0.20])
    p50 = {slots[0].start: 2000.0}
    common = dict(
        price_slots=slots, p50_by_slot=p50, departure=BASE + timedelta(hours=1),
        kwh_needed=1.0, charger_kw=4.0, now=BASE,
    )
    # fixed feed-in is independent of the spot price and subtracts the configured export fee.
    fixed = advise_charge_window(
        **common, export_model="fixed", fixed_feed_in_eur_per_kwh=0.04,
        export_fee_eur_per_kwh=0.01,
    )
    assert fixed is not None
    assert fixed["est_cost_eur"] == 0.03  # (0.04 - 0.01) * 1 kWh

    # Negative spot prices remain negative under spot-minus-tax; missing forecast entries are
    # treated as non-surplus and therefore retain the retail price.
    negative_slots = _slots([-0.05, 0.20])
    negative = advise_charge_window(
        price_slots=negative_slots, p50_by_slot={}, departure=BASE + timedelta(hours=1),
        kwh_needed=0.5, charger_kw=4.0, now=BASE,
        export_model="spot_minus_tax", energy_tax_eur_per_kwh=0.13,
    )
    assert negative is not None
    assert negative["est_cost_eur"] == -0.03


# ---- GET /api/advisor/ev-charge ----
#
# 20 kWh at 11 kW needs ceil(20/11/0.25) = 8 quarter-hours. The route keeps price slots with
# now <= start < departure, and departure is today 07:30 until that instant, then tomorrow.
# The 8th slot before 07:30 starts at 05:30, so advice is absent for 05:30 < local < 07:30.

_CHARGE_SLOTS = math.ceil(20.0 / 11.0 / 0.25)
_KWH_PER_SLOT = 20.0 / _CHARGE_SLOTS
_OVERNIGHT = 0.08  # price_for_hour for local hours 0–5
_SHOULDER = 0.12  # local hour 6
_MORNING_PEAK = 0.32  # local hours 7–8
_DAYTIME = 0.18  # local hours 9–16
_ENERGY_TAX = 0.13  # default prices.energy_tax_eur_per_kwh, spot_minus_tax surplus credit


def _app(tmp_path, clock=None):
    db = str(tmp_path / "ems.sqlite")
    clock_arg = {} if clock is None else {"clock": clock}
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=MockPriceSource(AMS, **clock_arg),
        solar_forecast=MockSolarForecastSource(AMS, **clock_arg),
        settings_store=SettingsStore(db),
    )


def _frozen_app(tmp_path, monkeypatch, local: datetime):
    """Pin the route clock and the mock price/forecast horizon to one Amsterdam instant.

    `freeze_wall_clock_utc` replaces `ems.web.api.datetime`. Mock sources bind their clock as a
    default argument at import, so the same instant is passed in explicitly as well.
    """
    from ems.tests.conftest import freeze_wall_clock_utc

    frozen_utc = local.astimezone(UTC)
    freeze_wall_clock_utc(monkeypatch, frozen_utc)
    return _app(tmp_path, clock=lambda: frozen_utc)


def _enable(client: TestClient) -> None:
    client.post("/api/settings", json={
        "ev.advice_enabled": True, "ev.departure_time": "07:30",
        "ev.charge_kwh": 20.0, "ev.charger_kw": 11.0,
    })


def _reason(solar_share_pct: int) -> str:
    text = "Cheapest 2h window before your 07:30 departure"
    if solar_share_pct > 0:
        return f"{text} \u2014 {solar_share_pct}% overlaps expected solar surplus."
    return f"{text}."


def _window(start: datetime, slot_prices: list[float], solar_share_pct: int) -> dict:
    """Exact advice for one contiguous 8-slot window.

    `end` is eight absolute quarter-hours after `start` (UTC), then shown in local time.
    Same-zone addition is wrong across the spring gap: 00:00+01 plus two wall-clock hours
    is the non-existent 02:00+01 label, while the real end is 03:00+02.
    """
    assert len(slot_prices) == _CHARGE_SLOTS
    cost = round(sum(price * _KWH_PER_SLOT for price in slot_prices), 2)
    end = (start.astimezone(UTC) + _CHARGE_SLOTS * timedelta(minutes=15)).astimezone(start.tzinfo)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "est_cost_eur": cost,
        "solar_share_pct": solar_share_pct,
        "slots": _CHARGE_SLOTS,
        "reason": _reason(solar_share_pct),
    }


def _overnight(start: datetime) -> dict:
    return _window(start, [_OVERNIGHT] * _CHARGE_SLOTS, 0)


def _boundary(start: datetime) -> dict:
    # 05:30 and 05:45 at 0.08, four hour-6 slots at 0.12, 07:00 and 07:15 at 0.32.
    prices = [_OVERNIGHT, _OVERNIGHT, _SHOULDER, _SHOULDER, _SHOULDER, _SHOULDER,
              _MORNING_PEAK, _MORNING_PEAK]
    return _window(start, prices, 0)


def _solar_midday(start: datetime) -> dict:
    # First P50 >= 1000 W is 09:45; daytime sticker 0.18, surplus credit 0.18 - 0.13.
    return _window(start, [_DAYTIME - _ENERGY_TAX] * _CHARGE_SLOTS, 100)


def test_ev_advisor_endpoint_null_when_disabled(tmp_path):
    with TestClient(_app(tmp_path)) as c:  # ev.advice_enabled defaults False
        body = c.get("/api/advisor/ev-charge").json()
    assert body == {"advice": None}


def test_ev_advisor_endpoint_returns_advice_shape_when_enabled(tmp_path, monkeypatch):
    # 04:00 is before the 05:30 cutoff: 04:00..07:15 is 14 slots, so advice exists on any CI clock.
    local = datetime(2026, 10, 24, 4, 0, tzinfo=AMS)
    with TestClient(_frozen_app(tmp_path, monkeypatch, local)) as c:
        _enable(c)
        body = c.get("/api/advisor/ev-charge").json()
    advice = body["advice"]
    assert advice is not None
    for key in ("start", "end", "est_cost_eur", "solar_share_pct", "slots", "reason"):
        assert key in advice
    assert advice["slots"] > 0
    assert "07:30" in advice["reason"]


# Ends use absolute quarters (#393). The #392 expectation for 2026-03-29 00:00 encoded the
# wall-clock label 02:00+01; the last quarter of that window really ends at 03:00+02.
@pytest.mark.parametrize(
    ("local", "expected"),
    [
        pytest.param(
            datetime(2026, 10, 24, 0, 15, tzinfo=AMS),
            _overnight(datetime(2026, 10, 24, 0, 15, tzinfo=AMS)),
            id="2026-10-24T00:15",
        ),
        pytest.param(
            datetime(2026, 10, 24, 2, 0, tzinfo=AMS),
            _overnight(datetime(2026, 10, 24, 2, 0, tzinfo=AMS)),
            id="2026-10-24T02:00",
        ),
        pytest.param(
            datetime(2026, 10, 24, 5, 30, tzinfo=AMS),
            _boundary(datetime(2026, 10, 24, 5, 30, tzinfo=AMS)),
            id="2026-10-24T05:30_last_fit",
        ),
        pytest.param(datetime(2026, 10, 24, 5, 31, tzinfo=AMS), None, id="2026-10-24T05:31"),
        pytest.param(datetime(2026, 10, 24, 5, 56, tzinfo=AMS), None, id="2026-10-24T05:56_ci"),
        pytest.param(datetime(2026, 10, 24, 7, 29, tzinfo=AMS), None, id="2026-10-24T07:29"),
        pytest.param(
            datetime(2026, 10, 24, 7, 30, tzinfo=AMS),
            _solar_midday(datetime(2026, 10, 24, 9, 45, tzinfo=AMS)),
            id="2026-10-24T07:30_rollover",
        ),
        pytest.param(
            datetime(2026, 10, 24, 23, 59, tzinfo=AMS),
            _overnight(datetime(2026, 10, 25, 0, 0, tzinfo=AMS)),
            id="2026-10-24T23:59",
        ),
        pytest.param(
            datetime(2026, 10, 25, 0, 0, tzinfo=AMS),
            _overnight(datetime(2026, 10, 25, 0, 0, tzinfo=AMS)),
            id="2026-10-25T00:00_dst_end",
        ),
        pytest.param(
            datetime(2026, 10, 25, 0, 15, tzinfo=AMS),
            _overnight(datetime(2026, 10, 25, 0, 15, tzinfo=AMS)),
            id="2026-10-25T00:15_dst_end",
        ),
        pytest.param(
            datetime(2026, 10, 25, 2, 0, tzinfo=AMS, fold=0),
            _overnight(datetime(2026, 10, 25, 2, 0, tzinfo=AMS, fold=0)),
            id="2026-10-25T02:00_fold0",
        ),
        pytest.param(
            datetime(2026, 10, 25, 2, 30, tzinfo=AMS, fold=0),
            _overnight(datetime(2026, 10, 25, 2, 30, tzinfo=AMS, fold=0)),
            id="2026-10-25T02:30_fold0",
        ),
        pytest.param(
            datetime(2026, 10, 25, 2, 45, tzinfo=AMS, fold=0),
            _overnight(datetime(2026, 10, 25, 2, 45, tzinfo=AMS, fold=0)),
            id="2026-10-25T02:45_fold0",
        ),
        pytest.param(
            datetime(2026, 10, 25, 2, 0, tzinfo=AMS, fold=1),
            _overnight(datetime(2026, 10, 25, 2, 0, tzinfo=AMS, fold=1)),
            id="2026-10-25T02:00_fold1",
        ),
        pytest.param(
            datetime(2026, 10, 25, 2, 30, tzinfo=AMS, fold=1),
            _overnight(datetime(2026, 10, 25, 2, 30, tzinfo=AMS, fold=1)),
            id="2026-10-25T02:30_fold1",
        ),
        pytest.param(
            datetime(2026, 10, 25, 4, 0, tzinfo=AMS),
            _overnight(datetime(2026, 10, 25, 4, 0, tzinfo=AMS)),
            id="2026-10-25T04:00_after_fold",
        ),
        pytest.param(
            datetime(2026, 10, 25, 5, 30, tzinfo=AMS),
            _boundary(datetime(2026, 10, 25, 5, 30, tzinfo=AMS)),
            id="2026-10-25T05:30_dst_end",
        ),
        pytest.param(datetime(2026, 10, 25, 5, 56, tzinfo=AMS), None, id="2026-10-25T05:56"),
        pytest.param(
            datetime(2026, 10, 25, 7, 30, tzinfo=AMS),
            _solar_midday(datetime(2026, 10, 25, 9, 45, tzinfo=AMS)),
            id="2026-10-25T07:30_rollover",
        ),
        pytest.param(
            datetime(2026, 3, 28, 23, 59, tzinfo=AMS),
            _overnight(datetime(2026, 3, 29, 0, 0, tzinfo=AMS)),
            id="2026-03-28T23:59",
        ),
        pytest.param(
            datetime(2026, 3, 29, 0, 0, tzinfo=AMS),
            _overnight(datetime(2026, 3, 29, 0, 0, tzinfo=AMS)),
            id="2026-03-29T00:00_dst_start",
        ),
        pytest.param(
            datetime(2026, 3, 29, 0, 15, tzinfo=AMS),
            _overnight(datetime(2026, 3, 29, 0, 15, tzinfo=AMS)),
            id="2026-03-29T00:15_dst_start",
        ),
        pytest.param(
            datetime(2026, 3, 29, 1, 45, tzinfo=AMS),
            _overnight(datetime(2026, 3, 29, 1, 45, tzinfo=AMS)),
            id="2026-03-29T01:45_before_gap",
        ),
        pytest.param(
            datetime(2026, 3, 29, 3, 0, tzinfo=AMS),
            _overnight(datetime(2026, 3, 29, 3, 0, tzinfo=AMS)),
            id="2026-03-29T03:00_after_gap",
        ),
        pytest.param(
            datetime(2026, 3, 29, 5, 30, tzinfo=AMS),
            _boundary(datetime(2026, 3, 29, 5, 30, tzinfo=AMS)),
            id="2026-03-29T05:30_dst_start",
        ),
        pytest.param(datetime(2026, 3, 29, 5, 56, tzinfo=AMS), None, id="2026-03-29T05:56"),
        pytest.param(
            datetime(2026, 3, 29, 7, 30, tzinfo=AMS),
            _solar_midday(datetime(2026, 3, 29, 9, 45, tzinfo=AMS)),
            id="2026-03-29T07:30_rollover",
        ),
        pytest.param(
            datetime(2026, 6, 14, 23, 59, tzinfo=AMS),
            _overnight(datetime(2026, 6, 15, 0, 0, tzinfo=AMS)),
            id="2026-06-14T23:59",
        ),
        pytest.param(
            datetime(2026, 6, 15, 0, 0, tzinfo=AMS),
            _overnight(datetime(2026, 6, 15, 0, 0, tzinfo=AMS)),
            id="2026-06-15T00:00",
        ),
        pytest.param(
            datetime(2026, 6, 15, 0, 15, tzinfo=AMS),
            _overnight(datetime(2026, 6, 15, 0, 15, tzinfo=AMS)),
            id="2026-06-15T00:15",
        ),
    ],
)
def test_ev_advisor_endpoint_fixed_clock(tmp_path, monkeypatch, local, expected):
    with TestClient(_frozen_app(tmp_path, monkeypatch, local)) as c:
        _enable(c)
        advice = c.get("/api/advisor/ev-charge").json()["advice"]
    assert advice == expected


class _ListedPrices:
    """Quarter-hour prices for the advisor route. No device and no network."""

    def __init__(self, slots: list[PriceSlot]) -> None:
        self._slots = slots

    def slots(self) -> list[PriceSlot]:
        return list(self._slots)


_TIE_PRICE = 0.10
_OTHER_PRICE = 0.50


def _at(year: int, month: int, day: int, hour: int, minute: int, *, fold: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=AMS, fold=fold)


def _tie_curve(local: datetime, early: datetime, later: datetime) -> list[PriceSlot]:
    """Two 8-slot windows at `_TIE_PRICE`, separated by one expensive quarter.

    Slots are stepped in UTC, so a spring-gap pair and both fall folds are real 15-minute steps.
    """
    midnight = datetime(local.year, local.month, local.day, tzinfo=AMS)
    end_day = midnight.date() + timedelta(days=2)
    end_local = datetime(end_day.year, end_day.month, end_day.day, tzinfo=AMS)
    cheap: set[datetime] = set()
    for origin in (early, later):
        base = origin.astimezone(UTC)
        for i in range(_CHARGE_SLOTS):
            cheap.add(base + i * timedelta(minutes=15))
    slots: list[PriceSlot] = []
    cur = midnight.astimezone(UTC)
    end = end_local.astimezone(UTC)
    while cur < end:
        price = _TIE_PRICE if cur in cheap else _OTHER_PRICE
        slots.append(PriceSlot(cur.astimezone(AMS), price))
        cur += timedelta(minutes=15)
    return slots


def _day_span(local: datetime) -> timedelta:
    midnight = datetime(local.year, local.month, local.day, tzinfo=AMS)
    nxt = local.date() + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=AMS)
    return end.astimezone(UTC) - midnight.astimezone(UTC)


# now, day quarters, start local, end local, start UTC, end UTC.
# The second cheap window begins nine quarters after `start` (one expensive quarter in between).
# At 05:30 that second window starts at 07:45, after the 07:30 departure, so only one window is
# eligible; every other clock below has two eligible windows at the same price.
_EQUAL_WINDOWS = [
    pytest.param(
        _at(2026, 10, 24, 0, 15), 96,
        "2026-10-24T00:15:00+02:00", "2026-10-24T02:15:00+02:00",
        "2026-10-23T22:15:00+00:00", "2026-10-24T00:15:00+00:00",
        id="2026-10-24T00:15",
    ),
    pytest.param(
        _at(2026, 10, 24, 2, 0), 96,
        "2026-10-24T02:00:00+02:00", "2026-10-24T04:00:00+02:00",
        "2026-10-24T00:00:00+00:00", "2026-10-24T02:00:00+00:00",
        id="2026-10-24T02:00",
    ),
    pytest.param(
        _at(2026, 10, 24, 5, 30), 96,
        "2026-10-24T05:30:00+02:00", "2026-10-24T07:30:00+02:00",
        "2026-10-24T03:30:00+00:00", "2026-10-24T05:30:00+00:00",
        id="2026-10-24T05:30",
    ),
    pytest.param(
        _at(2026, 10, 24, 7, 30), 96,
        "2026-10-24T07:30:00+02:00", "2026-10-24T09:30:00+02:00",
        "2026-10-24T05:30:00+00:00", "2026-10-24T07:30:00+00:00",
        id="2026-10-24T07:30",
    ),
    pytest.param(
        _at(2026, 3, 29, 0, 15), 92,
        "2026-03-29T00:15:00+01:00", "2026-03-29T03:15:00+02:00",
        "2026-03-28T23:15:00+00:00", "2026-03-29T01:15:00+00:00",
        id="2026-03-29T00:15",
    ),
    pytest.param(
        _at(2026, 3, 29, 1, 45), 92,
        "2026-03-29T01:45:00+01:00", "2026-03-29T04:45:00+02:00",
        "2026-03-29T00:45:00+00:00", "2026-03-29T02:45:00+00:00",
        id="2026-03-29T01:45",
    ),
    pytest.param(
        _at(2026, 3, 29, 3, 0), 92,
        "2026-03-29T03:00:00+02:00", "2026-03-29T05:00:00+02:00",
        "2026-03-29T01:00:00+00:00", "2026-03-29T03:00:00+00:00",
        id="2026-03-29T03:00",
    ),
    pytest.param(
        _at(2026, 3, 29, 5, 30), 92,
        "2026-03-29T05:30:00+02:00", "2026-03-29T07:30:00+02:00",
        "2026-03-29T03:30:00+00:00", "2026-03-29T05:30:00+00:00",
        id="2026-03-29T05:30",
    ),
    pytest.param(
        _at(2026, 3, 29, 7, 30), 92,
        "2026-03-29T07:30:00+02:00", "2026-03-29T09:30:00+02:00",
        "2026-03-29T05:30:00+00:00", "2026-03-29T07:30:00+00:00",
        id="2026-03-29T07:30",
    ),
    pytest.param(
        _at(2026, 10, 25, 0, 15), 100,
        "2026-10-25T00:15:00+02:00", "2026-10-25T02:15:00+02:00",
        "2026-10-24T22:15:00+00:00", "2026-10-25T00:15:00+00:00",
        id="2026-10-25T00:15",
    ),
    pytest.param(
        _at(2026, 10, 25, 2, 0, fold=0), 100,
        "2026-10-25T02:00:00+02:00", "2026-10-25T03:00:00+01:00",
        "2026-10-25T00:00:00+00:00", "2026-10-25T02:00:00+00:00",
        id="2026-10-25T02:00_fold0",
    ),
    pytest.param(
        _at(2026, 10, 25, 2, 30, fold=0), 100,
        "2026-10-25T02:30:00+02:00", "2026-10-25T03:30:00+01:00",
        "2026-10-25T00:30:00+00:00", "2026-10-25T02:30:00+00:00",
        id="2026-10-25T02:30_fold0",
    ),
    pytest.param(
        _at(2026, 10, 25, 2, 45, fold=0), 100,
        "2026-10-25T02:45:00+02:00", "2026-10-25T03:45:00+01:00",
        "2026-10-25T00:45:00+00:00", "2026-10-25T02:45:00+00:00",
        id="2026-10-25T02:45_fold0",
    ),
    pytest.param(
        _at(2026, 10, 25, 2, 0, fold=1), 100,
        "2026-10-25T02:00:00+01:00", "2026-10-25T04:00:00+01:00",
        "2026-10-25T01:00:00+00:00", "2026-10-25T03:00:00+00:00",
        id="2026-10-25T02:00_fold1",
    ),
    pytest.param(
        _at(2026, 10, 25, 2, 30, fold=1), 100,
        "2026-10-25T02:30:00+01:00", "2026-10-25T04:30:00+01:00",
        "2026-10-25T01:30:00+00:00", "2026-10-25T03:30:00+00:00",
        id="2026-10-25T02:30_fold1",
    ),
    pytest.param(
        _at(2026, 10, 25, 5, 30), 100,
        "2026-10-25T05:30:00+01:00", "2026-10-25T07:30:00+01:00",
        "2026-10-25T04:30:00+00:00", "2026-10-25T06:30:00+00:00",
        id="2026-10-25T05:30",
    ),
    pytest.param(
        _at(2026, 10, 25, 7, 30), 100,
        "2026-10-25T07:30:00+01:00", "2026-10-25T09:30:00+01:00",
        "2026-10-25T06:30:00+00:00", "2026-10-25T08:30:00+00:00",
        id="2026-10-25T07:30",
    ),
]


@pytest.mark.parametrize(
    ("local", "day_slots", "start_iso", "end_iso", "start_utc", "end_utc"),
    _EQUAL_WINDOWS,
)
def test_earliest_equal_cost_window_across_dst(
    tmp_path, monkeypatch, local, day_slots, start_iso, end_iso, start_utc, end_utc,
):
    """Frozen Amsterdam clock; two equally cheap 8-slot windows; the earliest absolute one wins."""
    assert _day_span(local) == timedelta(minutes=15) * day_slots
    early = datetime.fromisoformat(start_iso)
    later = (early.astimezone(UTC) + (_CHARGE_SLOTS + 1) * timedelta(minutes=15)).astimezone(AMS)
    slots = _tie_curve(local, early, later)
    on_day = [s for s in slots if s.start.astimezone(AMS).date() == local.date()]
    assert len(on_day) == day_slots
    cheap = [s for s in slots if s.eur_per_kwh == _TIE_PRICE]
    assert len(cheap) == _CHARGE_SLOTS * 2
    assert cheap[0].start.isoformat() == start_iso
    mock_on_day = [
        s for s in MockPriceSource(AMS, clock=lambda: local.astimezone(UTC)).slots()
        if s.start.astimezone(AMS).date() == local.date()
    ]
    assert len(mock_on_day) == day_slots

    from ems.tests.conftest import freeze_wall_clock_utc

    freeze_wall_clock_utc(monkeypatch, local.astimezone(UTC))
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=_ListedPrices(slots), solar_forecast=None,
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
    )
    with TestClient(app) as client:
        _enable(client)
        advice = client.get("/api/advisor/ev-charge").json()["advice"]
    assert advice == {
        "start": start_iso,
        "end": end_iso,
        "est_cost_eur": round(_TIE_PRICE * _KWH_PER_SLOT * _CHARGE_SLOTS, 2),
        "solar_share_pct": 0,
        "slots": _CHARGE_SLOTS,
        "reason": _reason(0),
    }
    assert datetime.fromisoformat(advice["start"]).astimezone(UTC).isoformat() == start_utc
    assert datetime.fromisoformat(advice["end"]).astimezone(UTC).isoformat() == end_utc
    assert "07:30" in advice["reason"]
