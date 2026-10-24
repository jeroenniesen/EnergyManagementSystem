"""Planning horizon through the end of the next local day (#329 / F-09)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.explain import build_plan_detail
from ems.planner.horizon import (
    MAX_HORIZON_SLOTS,
    end_of_next_local_day,
    slice_price_horizon,
)
from ems.planner.projection import BatteryModel, project_energy
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.summer import SummerConfig, plan_summer
from ems.planner.trading import TradingConfig, _price_horizon
from ems.planner.validator import validate_plan
from ems.savings import estimate_daily_savings_eur
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import MockPriceSource, PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")
_FIXTURE = Path(__file__).parent / "fixtures" / "late_pack_14d_prices.json"


def _slots(start: datetime, end: datetime, price_of) -> list[PriceSlot]:
    """Quarter-hours in absolute time, so a DST gap is skipped and a fold is kept."""
    cursor = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    out: list[PriceSlot] = []
    while cursor < end_utc:
        local = cursor.astimezone(AMS)
        out.append(PriceSlot(start=local, eur_per_kwh=float(price_of(local))))
        cursor += SLOT
    return out


def _midnight(day) -> datetime:
    return datetime.combine(day, time.min, AMS)


def _curve(moment: datetime) -> float:
    hour = moment.astimezone(AMS).hour
    if 14 <= hour < 16:
        return 0.12
    if 18 <= hour < 21:
        return 0.55
    if 0 <= hour < 6:
        return 0.06
    if 17 <= hour < 18:
        return 0.40
    return 0.22


def _load_w(moment: datetime) -> float:
    hour = moment.astimezone(AMS).hour
    return 3500.0 if 18 <= hour < 21 else 400.0


def _two_days(now: datetime, price_of=_curve) -> list[PriceSlot]:
    start = _midnight(now.astimezone(AMS).date())
    end = end_of_next_local_day(now, AMS)
    assert end is not None
    return _slots(start, end, price_of)


def _plan(prices, now: datetime):
    load = {p.start: _load_w(p.start) for p in prices}
    return plan_rule_based(
        prices, now, PlannerConfig(site_tz=AMS),
        soc_pct=12.0, load_w_by=load, usable_kwh=10.8, reserve_soc_pct=10.0,
        max_charge_w=4000.0,
    )


def _signature(plan: Plan):
    return [
        (s.start, s.intent, s.power_w, s.target_kwh)
        for s in plan.slots
    ]


def _future(prices, now: datetime) -> list[PriceSlot]:
    return [p for p in prices if p.start + SLOT > now]


def test_horizon_falls_back_to_96_without_tomorrow():
    """Missing the last slot of tomorrow keeps the historic 96-slot plan."""
    now = datetime(2026, 10, 20, 17, 0, tzinfo=AMS)
    full = _two_days(now)
    gapped = full[:-1]
    only_96 = _future(full, now)[:96]
    assert slice_price_horizon(gapped, now, AMS, fallback_slots=96) == _future(gapped, now)[:96]
    assert len(slice_price_horizon(gapped, now, AMS, fallback_slots=96)) == 96
    short = _plan(only_96, now)
    fallback = _plan(gapped, now)
    assert _signature(fallback) == _signature(short)
    assert len(fallback.slots) == len(short.slots) == 96
    assert all(s.power_w == o.power_w and s.target_kwh == o.target_kwh
               for s, o in zip(fallback.slots, short.slots, strict=True))


def test_horizon_falls_back_to_96_matches_fixed_arbitrage_golden():
    """One local day of the synthetic curve stays on the pre-#329 control digest."""
    import hashlib

    now = datetime(2026, 6, 27, 0, 0, tzinfo=AMS)
    prices = MockPriceSource(AMS, clock=lambda: now).slots()
    only = _future(prices, now)[:96]
    plan = plan_rule_based(only, now)
    digest = hashlib.sha256(repr([
        (s.intent, s.target_soc, s.target_kwh, s.power_w) for s in plan.slots
    ]).encode()).hexdigest()
    assert digest == (
        "11422461a3e8184006d9b06ae8ed53b34bd78d9457e8f5837a3ecf86870bd99b"
    )
    assert len(plan.slots) == 96


def test_afternoon_slots_enter_the_buy_pool_with_tomorrow_prices():
    """At 17:00, 14:00–16:00 tomorrow is economic once tomorrow's peak is visible."""
    now = datetime(2026, 10, 20, 17, 0, tzinfo=AMS)
    tomorrow = now.astimezone(AMS).date() + timedelta(days=1)
    full = _two_days(now)
    extended = _plan(full, now)
    short = _plan(_future(full, now)[:96], now)
    assert len(extended.slots) > 96
    assert len(extended.slots) <= MAX_HORIZON_SLOTS
    assert extended.buy_pool is not None and short.buy_pool is not None

    def _last_discharge(plan: Plan) -> datetime | None:
        starts = [s.start for s in plan.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD]
        return max(starts) if starts else None

    afternoon = [
        p for p in full
        if p.start.astimezone(AMS).date() == tomorrow and 14 <= p.start.astimezone(AMS).hour < 16
    ]
    assert len(afternoon) == 8
    ceiling = extended.buy_pool.max_buy_eur_per_kwh
    assert all(p.eur_per_kwh <= ceiling for p in afternoon)
    last = _last_discharge(extended)
    assert last is not None and last >= datetime(2026, 10, 21, 18, 0, tzinfo=AMS)
    in_pool = [p for p in afternoon if p.start < last]
    assert len(in_pool) == 8
    assert extended.buy_pool.pool_size >= len(in_pool)
    short_last = _last_discharge(short)
    assert short_last is not None and short_last < datetime(2026, 10, 21, 14, 0, tzinfo=AMS)
    assert _signature(short) != _signature(extended)


def test_dst_horizon_on_23h_and_25h_days():
    """Spring-forward (23h) and fall-back (25h) still end on the next local midnight."""
    spring = datetime(2026, 3, 28, 17, 0, tzinfo=AMS)
    spring_end = end_of_next_local_day(spring, AMS)
    assert spring_end is not None
    spring_prices = _slots(_midnight(spring.date()), spring_end, lambda _dt: 0.20)
    spring_window = slice_price_horizon(spring_prices, spring, AMS, fallback_slots=96)
    assert len(spring_window) == 120
    assert spring_window[-1].start + SLOT == spring_end
    spring_plan = _plan(spring_prices, spring)
    assert len(spring_plan.slots) == 120

    autumn = datetime(2026, 10, 24, 17, 0, tzinfo=AMS)
    autumn_end = end_of_next_local_day(autumn, AMS)
    assert autumn_end is not None
    autumn_prices = _slots(_midnight(autumn.date()), autumn_end, lambda _dt: 0.20)
    autumn_window = slice_price_horizon(autumn_prices, autumn, AMS, fallback_slots=96)
    assert len(autumn_window) == 128
    assert autumn_window[-1].start + SLOT == autumn_end

    gapped = spring_prices[:-1]
    assert len(slice_price_horizon(gapped, spring, AMS, fallback_slots=96)) == 96


def test_horizon_plan_length_caps_at_192():
    """A 25h day plus the next day is longer than 192 slots; the plan stops at 192."""
    now = datetime(2026, 10, 25, 0, 0, tzinfo=AMS)
    end = end_of_next_local_day(now, AMS)
    assert end is not None
    prices = _slots(now, end, lambda _dt: 0.20)
    assert len(_future(prices, now)) == 196
    window = slice_price_horizon(prices, now, AMS, fallback_slots=96)
    assert len(window) == MAX_HORIZON_SLOTS == 192
    plan = plan_rule_based(prices, now, PlannerConfig(site_tz=AMS))
    assert len(plan.slots) == 192


def test_adaptive_summer_and_trading_share_the_horizon():
    now = datetime(2026, 10, 20, 17, 0, tzinfo=AMS)
    prices = _two_days(now)
    load = {p.start: _load_w(p.start) for p in prices}
    forecast = [ForecastSlot(p.start, 0.0, 0.0, 0.0) for p in prices]
    adaptive = plan_adaptive(
        prices, forecast, now, soc_pct=20.0, load_w_by=load,
        cfg=AdaptiveConfig(usable_kwh=10.0, site_tz=AMS),
    )
    summer = plan_summer(
        prices, forecast, now, soc_pct=40.0,
        cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0, site_tz=AMS),
    )
    trading = _price_horizon(prices, now, TradingConfig())
    assert 96 < len(adaptive.slots) <= 192
    assert 96 < len(summer.slots) <= 192
    assert 96 < len(trading) <= 192
    gapped = prices[:-1]
    assert len(_price_horizon(gapped, now, TradingConfig(horizon_slots=96))) == 96


def test_validator_and_projection_accept_a_horizon_past_96_slots():
    start = datetime(2026, 6, 1, 12, 0, tzinfo=AMS)
    slots = []
    for index in range(120):
        moment = start + index * SLOT
        if index == 110:
            slots.append(PlanSlot(
                moment, BatteryIntent.GRID_CHARGE_TO_TARGET, "bad",
                target_soc=150.0, power_w=1000.0,
            ))
        else:
            slots.append(PlanSlot(moment, BatteryIntent.ALLOW_SELF_CONSUMPTION, "ok"))
    plan = Plan(created_at=start, slots=tuple(slots), strategy="winter")
    verdict = validate_plan(
        plan, soc_pct=50.0, data_quality="complete", min_reserve_soc=10.0,
    )
    assert any(f.code == "target_out_of_range" for f in verdict.findings)

    model = BatteryModel(
        usable_kwh=10.0, max_charge_w=4000.0, max_discharge_w=4000.0,
        round_trip_efficiency=0.9, reserve_soc_pct=10.0,
    )
    long_slots = [
        PlanSlot(start + i * SLOT, BatteryIntent.ALLOW_SELF_CONSUMPTION, "ok")
        for i in range(200)
    ]
    projected = project_energy(
        long_slots, start_soc_pct=50.0, solar_w_by={}, load_w_by={}, model=model,
    )
    assert len(projected) == 192
    detail = build_plan_detail(start, [], Plan(created_at=start, slots=tuple(long_slots)), None)
    assert len(detail["slots"]) == 192


def _fixture_prices() -> dict:
    spec = json.loads(_FIXTURE.read_text())
    by_date = {}
    for day in spec["days"]:
        start = datetime.fromisoformat(day["date"]).replace(tzinfo=AMS)
        by_date[day["date"]] = [
            PriceSlot(start + i * SLOT, float(eur))
            for i, eur in enumerate(day["eur_per_kwh"])
        ]
    return by_date


def test_horizon_14_day_replay_counts_extra_afternoon_buy_slots():
    """Synthetic 14-day prices: savings delta and extra 14:00–16:00 buy slots."""
    by_date = _fixture_prices()
    dates = sorted(by_date)
    extra_afternoon = 0
    pool_afternoon = 0
    savings_short = 0.0
    savings_long = 0.0
    for date in dates[:-1]:
        today = by_date[date]
        tomorrow = by_date[dates[dates.index(date) + 1]]
        now = datetime.fromisoformat(date).replace(hour=17, tzinfo=AMS)
        short_prices = [p for p in today if p.start + SLOT > now]
        long_prices = short_prices + tomorrow
        short = _plan(short_prices, now)
        extended = _plan(long_prices, now)
        price_by = {p.start: p.eur_per_kwh for p in long_prices}
        savings_short += estimate_daily_savings_eur(short, price_by)
        savings_long += estimate_daily_savings_eur(extended, price_by)
        next_day = tomorrow[0].start.astimezone(AMS).date()
        short_charge = {
            s.start for s in short.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
        }
        discharges = [
            s.start for s in extended.slots
            if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD
        ]
        last = max(discharges) if discharges else None
        ceiling = extended.buy_pool.max_buy_eur_per_kwh if extended.buy_pool else None
        for slot in extended.slots:
            local = slot.start.astimezone(AMS)
            if slot.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
                continue
            if (
                local.date() == next_day
                and 14 <= local.hour < 16
                and slot.start not in short_charge
            ):
                extra_afternoon += 1
        if last is not None and ceiling is not None:
            for price in tomorrow:
                local = price.start.astimezone(AMS)
                if (
                    local.date() == next_day
                    and 14 <= local.hour < 16
                    and price.start < last
                    and price.eur_per_kwh <= ceiling + 1e-9
                ):
                    pool_afternoon += 1
    assert extra_afternoon == 7
    assert pool_afternoon == 88
    assert savings_short == 0.0
    assert round(savings_long, 2) == 23.31

