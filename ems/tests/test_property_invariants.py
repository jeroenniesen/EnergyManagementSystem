"""Deterministic generated-input invariants for planning, safety, and economics.

These deliberately use a tiny local generator instead of a third-party property-testing
dependency so they remain reproducible in the production test environment.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ems.control.mode_controller import ModeController
from ems.control.safety import SafetyValidator
from ems.domain import BatteryIntent, PhysicalMode
from ems.economics import EconomicSnapshot
from ems.lifecycle import Lifecycle
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.sources.battery import MockBatteryDriver
from ems.sources.prices import PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")
START = datetime(2026, 1, 1, tzinfo=AMS)


def _generated_prices(seed: int, count: int = 32) -> list[PriceSlot]:
    """Cheap deterministic pseudo-random prices, including flat and negative cases."""
    return [
        PriceSlot(START + timedelta(minutes=15 * i), ((seed * 37 + i * 19) % 61 - 10) / 100)
        for i in range(count)
    ]


@pytest.mark.parametrize("seed", range(20))
def test_generated_plans_have_bounded_targets_and_nonnegative_energy(seed: int) -> None:
    prices = _generated_prices(seed)
    plan = plan_rule_based(
        prices,
        START,
        PlannerConfig(negative_price_soak=seed % 3 == 0),
        soc_pct=(seed * 17) % 101,
        reserve_soc_pct=(seed * 7) % 35,
        usable_kwh=5.0 + seed % 10,
        max_charge_w=1500.0 + seed * 100,
        load_w_by={p.start: float((seed * 101 + i * 73) % 2500) for i, p in enumerate(prices)},
    )
    assert plan.target_soc is None or 0.0 <= plan.target_soc <= 100.0
    for slot in plan.slots:
        assert slot.target_kwh is None or slot.target_kwh >= 0.0
        assert slot.power_w is None or slot.power_w >= 0.0
        if slot.floor_soc is not None:
            assert 0.0 <= slot.floor_soc <= 100.0
        if slot.intent is BatteryIntent.DISCHARGE_FOR_LOAD:
            assert slot.floor_soc is None or slot.floor_soc >= 0.0


@pytest.mark.parametrize(
    "soc,reserve,margin",
    [(s, r, m) for s in (0, 10, 50, 100) for r in (0, 10, 50) for m in (0, 1.5)],
)
def test_reserve_floor_predicate_is_monotonic(soc: float, reserve: float, margin: float) -> None:
    reached = SafetyValidator.reserve_reached(soc, reserve, margin_pp=margin)
    assert reached == (soc <= reserve + margin)
    assert SafetyValidator.reserve_reached(reserve, reserve, margin_pp=margin)


@pytest.mark.parametrize("seed", range(20))
def test_economic_no_trade_when_discharge_does_not_cover_delivered_cost(seed: int) -> None:
    snapshot = EconomicSnapshot(
        import_price_eur_per_kwh=0.05 + (seed % 10) / 100,
        export_price_eur_per_kwh=0.10 + (seed % 7) / 100,
        round_trip_efficiency=0.75 + (seed % 20) / 100,
        degradation_eur_per_kwh=0.01 + (seed % 4) / 100,
        risk_margin_eur_per_kwh=0.005 + (seed % 3) / 100,
    )
    delivered = snapshot.delivered_energy_cost()
    assert snapshot.net_benefit(delivered - 1e-9) <= 0.0
    assert snapshot.net_benefit(delivered) == pytest.approx(0.0)


@pytest.mark.parametrize("efficiency", [0.5, 0.75, 0.9, 1.0])
def test_break_even_is_inverse_of_delivered_cost(efficiency: float) -> None:
    snapshot = EconomicSnapshot(
        import_price_eur_per_kwh=0.2,
        export_price_eur_per_kwh=0.4,
        round_trip_efficiency=efficiency,
        degradation_eur_per_kwh=0.03,
        risk_margin_eur_per_kwh=0.02,
    )
    break_even = snapshot.break_even_import_price(0.4)
    assert snapshot.delivered_energy_cost(break_even) == pytest.approx(0.4)


def _controlling_lc():
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    t0 = datetime(2026, 3, 1, 12, tzinfo=AMS)
    lc.start(t0)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(t0)
    return lc


@pytest.mark.parametrize("seed", range(15))
def test_mode_controller_daily_writes_never_exceed_routine_plus_commitment_caps(seed: int) -> None:
    routine_cap = 3 + seed % 5
    commit_cap = 2 + seed % 4
    d = MockBatteryDriver()
    ctl = ModeController(
        d,
        _controlling_lc(),
        dry_run=False,
        max_switches_per_day=routine_cap,
        max_commitment_switches_per_day=commit_cap,
        min_dwell_seconds=0,
    )
    t = datetime(2026, 3, 1, 12, tzinfo=AMS)
    for step in range(40 + seed):
        commitment = (step + seed) % 2 == 0
        intent = (
            BatteryIntent.GRID_CHARGE_TO_TARGET
            if d.current_mode() is PhysicalMode.IDLE
            else BatteryIntent.HOLD_RESERVE
        )
        dec = ctl.decide(intent, t, target_soc=90, commitment=commitment)
        if dec.outcome == "cap_reached":
            break
        t += timedelta(seconds=1)
    assert ctl.switches_today <= routine_cap
    assert ctl.commitment_switches_today <= commit_cap
    assert ctl.switches_today + ctl.commitment_switches_today <= routine_cap + commit_cap
