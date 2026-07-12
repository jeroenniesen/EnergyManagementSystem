"""Summer 'solar-first' strategy: fill from the panels, run the night on the battery, grid-charge
only the shortfall in the cheapest pre-sunset slots. Pure — canned prices + forecast, no hardware.
"""
from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.schedule import SLOT
from ems.planner.summer import SummerConfig, plan_summer, sunset_after
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

# A daytime "now": noon UTC. Build a horizon of quarter-hour price slots.
T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _prices(n: int, eur: float = 0.10) -> list[PriceSlot]:
    return [PriceSlot(T0 + i * SLOT, eur) for i in range(n)]


def _forecast(watts: list[float]) -> list[ForecastSlot]:
    # p10 = p50 here (so commitment sizing == expected) unless a test needs otherwise.
    return [ForecastSlot(T0 + i * SLOT, w, w, w) for i, w in enumerate(watts)]


def _cfg(**kw) -> SummerConfig:
    base = dict(usable_kwh=10.0, target_soc_pct=80.0, round_trip_efficiency=1.0,
                max_charge_w=4000.0, expected_load_w=0.0, allow_grid_topup=True,
                max_topup_price_eur_per_kwh=0.30)
    base.update(kw)
    return SummerConfig(**base)


def test_grid_charge_slots_carry_target_soc_power_not_full():
    # Night now (no sun) + low SoC → must grid-charge the shortfall. Those slots must carry the
    # night-carry TARGET (80%, not 100%) and a power, so the driver charges the shortfall, not full.
    prices = _prices(16, eur=0.10)
    fc = _forecast([0.0] * 16)  # no daylight
    plan = plan_summer(prices, fc, T0, soc_pct=10.0, cfg=_cfg(target_soc_pct=80.0))
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert charge, "a low-SoC dark night should grid-charge"
    for s in charge:
        assert s.target_soc == 80.0  # NOT 100 — the calculated night target
        assert s.power_w == 4000.0 and s.target_kwh and s.target_kwh > 0
    assert plan.strategy == "summer" and plan.target_soc == 80.0  # plan-level contract


def test_plan_slot_end_defaults_to_start_plus_slot():
    plan = plan_summer(_prices(4), _forecast([0.0] * 4), T0, soc_pct=50.0, cfg=_cfg())
    assert plan.slots[0].slot_end == plan.slots[0].start + SLOT


def test_solar_covers_the_night_so_no_grid_topup():
    # 16 slots, all bright sun (3 kW) -> plenty to fill 50% -> 80% (3 kWh) from solar alone.
    prices = _prices(16)
    fc = _forecast([3000.0] * 16)
    plan = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_cfg())
    intents = {s.intent for s in plan.slots}
    assert BatteryIntent.GRID_CHARGE_TO_TARGET not in intents  # solar-first, no grid needed
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in plan.slots)


def test_grid_tops_up_only_the_shortfall_in_the_cheapest_preset_slots():
    # No sun at all -> the whole night target must come from the grid. 50%->80% of 10 kWh = 3 kWh;
    # at 4 kW a slot stores 1 kWh, so 3 slots are needed.
    prices = [PriceSlot(T0 + i * SLOT, 0.10 + 0.01 * i) for i in range(16)]  # rising price
    fc = _forecast([0.0] * 16)  # no solar
    plan = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_cfg(max_charge_w=4000.0))
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert len(charge) == 3  # exactly the shortfall, not the whole horizon
    # The three cheapest slots are the first three (rising price) -> earliest starts.
    assert [s.start for s in charge] == [T0, T0 + SLOT, T0 + 2 * SLOT]


def test_solar_confidence_knob_changes_grid_topup():
    # Modest daytime sun that EXACTLY covers the night target at full confidence but falls short
    # when discounted. The knob must flip the grid top-up: trusting the forecast buys nothing;
    # being cautious buys the assumed shortfall. (This is the fix for "buys grid power it didn't
    # need" — the old hard P10=0.6× behaves like solar_confidence≈0.6.)
    prices = _prices(16, eur=0.10)
    fc = _forecast([1000.0] * 16)  # 50%->80% of 10 kWh = 3 kWh; full sun gives 4 kWh, half gives 2
    trusting = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_cfg(solar_confidence=1.0))
    cautious = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_cfg(solar_confidence=0.5))
    n_trust = sum(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in trusting.slots)
    n_cautious = sum(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in cautious.slots)
    assert n_trust == 0, "trusting the forecast: solar covers the target, no grid buy"
    assert n_cautious > 0, "cautious: assumes less sun, buys the shortfall"


def test_grid_topup_can_be_disabled():
    prices = _prices(16)
    fc = _forecast([0.0] * 16)
    plan = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_cfg(allow_grid_topup=False))
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in plan.slots)


def test_price_cap_blocks_expensive_topup():
    # Shortfall exists but every slot is above the top-up price cap -> no grid charging.
    prices = _prices(16, eur=0.40)
    fc = _forecast([0.0] * 16)
    plan = plan_summer(prices, fc, T0, soc_pct=50.0,
                       cfg=_cfg(max_topup_price_eur_per_kwh=0.30))
    assert not [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]


def test_overnight_slots_run_the_house_on_the_battery():
    # First 4 slots sunny, rest dark -> the dark slots are "overnight" self-consumption.
    prices = _prices(12)
    fc = _forecast([2500.0] * 4 + [0.0] * 8)
    plan = plan_summer(prices, fc, T0, soc_pct=80.0, cfg=_cfg())
    night = plan.slots[4:]
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in night)
    assert any("overnight" in s.reason for s in night)


def test_already_full_needs_no_grid():
    prices = _prices(16)
    fc = _forecast([0.0] * 16)
    plan = plan_summer(prices, fc, T0, soc_pct=85.0, cfg=_cfg(target_soc_pct=80.0))
    assert not [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]


def test_grid_topup_never_scheduled_after_todays_sunset():
    # Today's sun (slots 0-3) is too weak to fill the battery, and the OVERNIGHT slots (8+) are the
    # cheapest. A naive planner would grid-charge overnight; we must only charge before sunset
    # (today), leaving the night for discharging the house.
    prices = [PriceSlot(T0 + i * SLOT, 0.20 if i < 8 else 0.05) for i in range(48)]
    fc = _forecast([500.0] * 4 + [0.0] * 44)  # a little sun this afternoon, then dark
    plan = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_cfg())
    sunset = (T0 + 3 * SLOT)  # last daytime slot start
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert charge, "a shortfall should force some grid top-up"
    assert all(s.start <= sunset for s in charge)  # never overnight, despite cheaper night prices


def test_night_now_does_not_credit_tomorrows_solar():
    # It's night: the only sun in the horizon is tomorrow (slots 24+). That solar must NOT be
    # credited against carrying tonight, so a real shortfall still triggers grid top-up tonight.
    prices = [PriceSlot(T0 + i * SLOT, 0.10) for i in range(48)]
    fc = _forecast([0.0] * 24 + [3000.0] * 24)  # dark now, sunny tomorrow
    plan = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_cfg())
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert charge, "tomorrow's sun must not be credited against tonight's target"
    sunrise = T0 + 24 * SLOT
    assert all(s.start < sunrise for s in charge)  # topped up tonight, before the sun returns


def test_topup_slot_count_respects_efficiency():
    # rte 0.81 -> eta 0.9: a 4 kW slot stores 0.9 kWh. 50%->80% of 10 kWh = 3 kWh -> ceil(3/0.9)=4.
    prices = _prices(16, eur=0.10)
    fc = _forecast([0.0] * 16)
    plan = plan_summer(prices, fc, T0, soc_pct=50.0,
                       cfg=_cfg(round_trip_efficiency=0.81, max_charge_w=4000.0))
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert len(charge) == 4


def test_sunset_after_finds_the_next_daylight_end():
    # Sun in slots 2..5 (others dark) -> sunset is the start of slot 5.
    fc = _forecast([0.0, 0.0, 1500.0, 2500.0, 2500.0, 800.0, 0.0, 0.0])
    assert sunset_after(fc, T0) == T0 + 5 * SLOT
    # No sun at all -> None.
    assert sunset_after(_forecast([0.0] * 6), T0) is None
    # Stops at the first daylight run (today), ignoring tomorrow's sun after a gap.
    two_days = _forecast([2000.0, 2000.0] + [0.0] * 4 + [2000.0, 2000.0])
    assert sunset_after(two_days, T0) == T0 + 1 * SLOT


def test_empty_prices_yields_empty_plan():
    assert plan_summer([], [], T0, soc_pct=50.0, cfg=_cfg()).slots == ()
    # past-only prices are filtered out too
    past = [PriceSlot(T0 - 4 * SLOT, 0.1)]
    assert plan_summer(past, [], T0, soc_pct=50.0, cfg=_cfg()).slots == ()


def test_negative_price_soak_charges_below_zero_even_with_grid_topup_off():
    # allow_grid_topup OFF: normally no grid charging at all (see test_grid_topup_can_be_disabled).
    # With the soak on, a sub-zero slot is STILL a charge slot — you are PAID to consume — bounded
    # by the existing shortfall sizing. A real shortfall exists (50%->80% of 10 kWh, no sun).
    prices = [PriceSlot(T0 + i * SLOT, -0.05 if i == 2 else 0.20) for i in range(16)]
    fc = _forecast([0.0] * 16)  # no sun → a genuine night shortfall to fill
    off = plan_summer(prices, fc, T0, soc_pct=50.0, cfg=_cfg(allow_grid_topup=False))
    on = plan_summer(prices, fc, T0, soc_pct=50.0,
                     cfg=_cfg(allow_grid_topup=False, negative_price_soak=True))
    off_charge = [s for s in off.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    on_charge = [s for s in on.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert off_charge == []  # grid top-up off + no soak → nothing bought
    soaked = [s for s in on_charge if s.start == T0 + 2 * SLOT]
    assert soaked, "the sub-zero slot must be soaked as a charge slot"
    assert "paid to charge" in soaked[0].reason
    # Only the sub-zero slot is soaked — the €0.20 slots stay off (grid top-up is disabled).
    assert all(s.start == T0 + 2 * SLOT for s in on_charge)
