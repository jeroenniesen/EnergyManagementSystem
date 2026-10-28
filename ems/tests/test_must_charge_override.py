"""Resident must-charge override (#322 / F-41). Fixed clock — never datetime.now."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.budgets import (
    ac_grid_kwh_per_slot,
    cap_reason,
    clamp_cycle_budget,
    clamp_grid_charge,
    cycle_budget_reason,
)
from ems.planner.must_charge import (
    BATTERY_LIMIT_REASON,
    apply_resident_overlay,
    explain_resident_override,
    price_cap_unreachable_reason,
)
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.prices import PriceSlot

NOW = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)
DEADLINE = datetime(2026, 1, 16, 2, 0, tzinfo=UTC)
CHEAP = datetime(2026, 1, 16, 1, 0, tzinfo=UTC)
_UTC = ZoneInfo("UTC")


def _prices(eur_of) -> list[PriceSlot]:
    out: list[PriceSlot] = []
    cursor = NOW
    while cursor < DEADLINE:
        out.append(PriceSlot(cursor, eur_of(cursor)))
        cursor += SLOT
    return out


def _plan(prices: list[PriceSlot]) -> Plan:
    return Plan(
        created_at=NOW,
        slots=tuple(
            PlanSlot(p.start, BatteryIntent.ALLOW_SELF_CONSUMPTION, "idle")
            for p in prices
        ),
        strategy="winter",
    )


def _adaptive(**kw) -> AdaptiveConfig:
    base = dict(
        usable_kwh=10.0,
        reserve_soc_pct=10.0,
        round_trip_efficiency=0.90,
        max_charge_w=4000.0,
        max_daily_grid_charge_kwh=40.0,
        max_cycles_per_day=5.0,
        charged_today_kwh=0.0,
        discharged_today_kwh=0.0,
        already_bought_today_kwh=0.0,
        site_tz=_UTC,
        must_charge_capability_ready=True,
        must_charge_device_min_target_soc=5.0,
        must_charge_max_price_eur_per_kwh=0.40,
        resident_must_charge_target_soc=55.0,
        resident_must_charge_deadline=DEADLINE,
    )
    base.update(kw)
    return AdaptiveConfig(**base)


def _grid_kwh(plan: Plan) -> float:
    total = 0.0
    for slot in plan.slots:
        if slot.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
            continue
        watts = slot.power_w if slot.power_w is not None else 0.0
        total += watts * 0.25 / 1000.0
    return total


def test_resident_override_charges_the_single_cheapest_slot():
    prices = _prices(lambda start: 0.05 if start == CHEAP else 0.20)
    cfg = _adaptive()
    plan = apply_resident_overlay(_plan(prices), prices, NOW, soc_pct=50.0, adaptive_cfg=cfg)
    charged = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert [s.start for s in charged] == [CHEAP]
    assert charged[0].reason == (
        "Voorzorgslading: vul tot 55% vóór 02:00 [plafond-instelling €0.40/kWh]"
    )
    assert charged[0].target_soc == 55.0
    assert charged[0].deadline == DEADLINE
    assert explain_resident_override(plan, prices, NOW, soc_pct=50.0, adaptive_cfg=cfg) is None


def test_resident_override_expires_at_deadline_and_when_target_is_reached():
    prices = _prices(lambda start: 0.05 if start == CHEAP else 0.20)
    cfg = _adaptive()
    base = _plan(prices)
    after_deadline = apply_resident_overlay(
        base, prices, DEADLINE, soc_pct=50.0, adaptive_cfg=cfg,
    )
    assert after_deadline.slots == base.slots
    reached = apply_resident_overlay(
        base, prices, NOW, soc_pct=55.0, adaptive_cfg=cfg,
    )
    assert reached.slots == base.slots
    assert [s.intent for s in reached.slots] == [
        BatteryIntent.ALLOW_SELF_CONSUMPTION for _ in reached.slots
    ]


def test_resident_override_respects_price_cap_daily_ceiling_and_cycle_budget():
    # Price cap: every slot is above the ceiling, so nothing is grid-charged.
    pricey = _prices(lambda _start: 0.50)
    price_cfg = _adaptive(
        must_charge_max_price_eur_per_kwh=0.30,
        resident_must_charge_target_soc=100.0,
    )
    price_plan = apply_resident_overlay(
        _plan(pricey), pricey, NOW, soc_pct=20.0, adaptive_cfg=price_cfg,
    )
    assert _grid_kwh(price_plan) == 0.0
    assert explain_resident_override(
        price_plan, pricey, NOW, soc_pct=20.0, adaptive_cfg=price_cfg,
    ) == price_cap_unreachable_reason(0.30)
    assert price_cap_unreachable_reason(0.30) == "Prijsplafond: niet genoeg slots onder €0.30/kWh"

    # Daily ceiling: a large target is clipped to 1 kWh and the plan says so.
    wide = _prices(lambda _start: 0.10)
    cap_cfg = _adaptive(
        resident_must_charge_target_soc=100.0,
        max_daily_grid_charge_kwh=1.0,
    )
    capped = apply_resident_overlay(_plan(wide), wide, NOW, soc_pct=20.0, adaptive_cfg=cap_cfg)
    capped = clamp_grid_charge(
        capped, 0.0, 1.0, ac_grid_kwh_per_slot(4000.0), tz=_UTC,
    )
    by_day: dict = {}
    for slot in capped.slots:
        if slot.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
            continue
        day = slot.start.astimezone(_UTC).date()
        by_day[day] = by_day.get(day, 0.0) + (slot.power_w or 0.0) * 0.25 / 1000.0
    assert by_day == {
        datetime(2026, 1, 15, tzinfo=UTC).date(): 1.0,
        datetime(2026, 1, 16, tzinfo=UTC).date(): 1.0,
    }
    assert all(kwh <= 1.0 for kwh in by_day.values())
    assert explain_resident_override(
        capped, wide, NOW, soc_pct=20.0, adaptive_cfg=cap_cfg,
    ) == cap_reason(1.0)
    assert cap_reason(1.0) == "Dagplafond 1 kWh bereikt"

    # EFC budget already exhausted: no grid charge, and the plan names the budget.
    cycle_cfg = _adaptive(resident_must_charge_target_soc=100.0, max_cycles_per_day=0.0)
    cycled = apply_resident_overlay(_plan(wide), wide, NOW, soc_pct=20.0, adaptive_cfg=cycle_cfg)
    cycled = clamp_cycle_budget(
        cycled, 0.0, 0.0, 0.0, 10.0,
        round_trip_efficiency=0.90, charge_fallback_w=4000.0, tz=_UTC,
    )
    assert _grid_kwh(cycled) == 0.0
    assert explain_resident_override(
        cycled, wide, NOW, soc_pct=20.0, adaptive_cfg=cycle_cfg,
    ) == cycle_budget_reason(0.0)
    assert cycle_budget_reason(0.0) == "Cycle-budget op (0 EFC)"


def test_battery_power_cannot_reach_target_before_a_short_deadline():
    short = NOW + timedelta(minutes=15)
    prices = [PriceSlot(NOW, 0.10)]
    cfg = _adaptive(
        resident_must_charge_deadline=short,
        resident_must_charge_target_soc=100.0,
    )
    plan = apply_resident_overlay(_plan(prices), prices, NOW, soc_pct=20.0, adaptive_cfg=cfg)
    assert _grid_kwh(plan) == 1.0
    assert explain_resident_override(
        plan, prices, NOW, soc_pct=20.0, adaptive_cfg=cfg,
    ) == BATTERY_LIMIT_REASON
    assert BATTERY_LIMIT_REASON == "Batterijvermogen: doel niet haalbaar vóór de deadline"
