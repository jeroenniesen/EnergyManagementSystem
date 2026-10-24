"""#327: one buy ceiling, and no planner still compares a slot price to the raw peak.

The static check is a source scan (the same job as ``rg``). It reads the four planner
modules that decide a buy and rejects a comparison of a slot price to ``peak`` / ``peak_min``.
Passing the cheapest peak *into* ``max_buy_for_peak`` is the supported use.

``ems/tests/test_late_pack_14day.py`` is outside this change. It keeps a local copy of the
pre-#328 adaptive pool (``eur_per_kwh < peak_min``) as the earliest-first baseline, so the
scan does not include it.
"""
import re
from datetime import UTC, datetime
from pathlib import Path

from ems.domain import BatteryIntent
from ems.planner.economics import max_buy_for_peak, max_buy_for_planner_config
from ems.planner.recovery import build_catch_up_plan
from ems.planner.rule_based import PlannerConfig
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.prices import PriceSlot

_PLANNER = Path(__file__).resolve().parents[1] / "planner"
_BUY_PATHS = ("rule_based.py", "adaptive.py", "summer.py", "recovery.py")
# A buy gate that compares a slot price to the raw peak, skipping the helper.
_RAW_PEAK_COMPARE = re.compile(
    r"(?:eur_per_kwh|price)\s*<=?\s*peak(?:_min)?\b|<\s*peak_min\b"
)

_T0 = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)


def test_planner_buy_paths_use_the_helper_and_not_a_raw_peak_compare():
    """#327 AC3: documented source scan. See the module docstring for the exclusion."""
    for name in _BUY_PATHS:
        text = (_PLANNER / name).read_text(encoding="utf-8")
        assert "max_buy_for_peak" in text or "max_buy_for_planner_config" in text, name
        assert _RAW_PEAK_COMPARE.search(text) is None, name


def test_recovery_max_buy_matches_winter_on_the_same_peak():
    """#327 AC1 parity: same peak, wear, risk and RTE → same ceiling, same buy/no-buy."""
    peak, rte, deg, risk = 0.40, 0.85, 0.03, 0.01
    ceiling = max_buy_for_peak(peak, rte=rte, deg=deg, risk=risk)
    winter = max_buy_for_planner_config(
        peak,
        PlannerConfig(
            round_trip_efficiency=rte,
            degradation_eur_per_kwh=deg,
            risk_margin_eur_per_kwh=risk,
        ),
    )
    assert winter == ceiling
    assert 0.30 <= ceiling < 0.31

    deadline = _T0 + 8 * SLOT
    slots = []
    for i in range(10):
        if i == 8:
            slots.append(PlanSlot(_T0 + i * SLOT, BatteryIntent.DISCHARGE_FOR_LOAD, "peak"))
        else:
            slots.append(PlanSlot(_T0 + i * SLOT, BatteryIntent.ALLOW_SELF_CONSUMPTION, "x"))
    plan = Plan(
        created_at=_T0, slots=tuple(slots), strategy="winter",
        target_soc=80.0, deadline=deadline,
    )
    prices = []
    for i in range(10):
        if i == 8:
            eur = 0.40
        elif i == 2:
            eur = 0.30
        elif i == 3:
            eur = 0.31
        else:
            eur = 0.90
        prices.append(PriceSlot(_T0 + i * SLOT, eur))
    caught = build_catch_up_plan(
        plan, _T0, soc_pct=70.0, prices=prices, usable_kwh=10.0, reserve_soc_pct=10.0,
        max_charge_w=4000.0, round_trip_efficiency=rte,
        degradation_eur_per_kwh=deg, risk_margin_eur_per_kwh=risk,
    )
    charge_prices = {
        next(p.eur_per_kwh for p in prices if p.start == s.start)
        for s in caught.plan.slots
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    }
    assert 0.30 in charge_prices
    assert 0.31 not in charge_prices
    assert all(price <= ceiling for price in charge_prices)
