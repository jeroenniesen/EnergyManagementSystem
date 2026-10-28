"""#402: plan-preview recovery (_run_recovery) must pass the same deg/risk as the live planner."""
import asyncio
from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.economics import max_buy_for_peak
from ems.planner.recovery import recover_if_needed
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.validator import PlanValidation
from ems.sources.prices import PriceSlot
from ems.web.api import _run_recovery

T0 = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
DEADLINE = T0 + 8 * SLOT
PEAK_EUR, RTE, DEG, RISK = 0.40, 0.85, 0.03, 0.01
SIZING = dict(
    usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0, round_trip_efficiency=RTE,
)


def _price(i: int, eur: float) -> PriceSlot:
    return PriceSlot(start=T0 + i * SLOT, eur_per_kwh=eur)


def _missed_plan() -> Plan:
    slots = [
        PlanSlot(T0 + i * SLOT,
                 BatteryIntent.DISCHARGE_FOR_LOAD if i == 8
                 else BatteryIntent.ALLOW_SELF_CONSUMPTION, "x")
        for i in range(10)
    ]
    return Plan(created_at=T0, slots=tuple(slots), strategy="winter",
                target_soc=80.0, deadline=DEADLINE)


def _prices() -> list[PriceSlot]:
    # Slot 3 (€0.31) is between live max_buy (0.306) and deg=risk=0 max_buy (0.34).
    return [_price(i, PEAK_EUR if i == 8 else (0.30 if i == 2 else (0.31 if i == 3 else 0.90)))
            for i in range(10)]


def _charge_starts(plan: Plan) -> tuple[datetime, ...]:
    return tuple(sorted(
        s.start for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    ))


def _live_recovery(plan: Plan, prices: list[PriceSlot], now: datetime):
    """Same recover_if_needed kwargs as ControlService._plan_with_recovery_uncached."""
    return recover_if_needed(
        plan, now, soc_pct=70.0, prices=prices, enabled=True,
        degradation_eur_per_kwh=DEG, risk_margin_eur_per_kwh=RISK, **SIZING,
    )


def test_preview_recovery_matches_live_planner_buy_ceiling():
    plan = _missed_plan()
    prices = _prices()
    now = T0
    expected_max_buy = max_buy_for_peak(PEAK_EUR, rte=RTE, deg=DEG, risk=RISK)
    assert 0.30 < expected_max_buy < 0.31

    _, _, live_catch = _live_recovery(plan, prices, now)
    assert live_catch is not None
    live_starts = _charge_starts(live_catch.plan)

    seen: dict = {}

    def _validator(catch_plan, _now):
        seen["plan"] = catch_plan
        return PlanValidation(status="valid")

    asyncio.run(_run_recovery(
        plan, now, soc_pct=70.0, prices=prices, enabled=True, tz=UTC,
        cache_store=None, notifier=None, audit_store=None, validate_fn=_validator,
        degradation_eur_per_kwh=DEG, risk_margin_eur_per_kwh=RISK, **SIZING,
    ))
    preview_starts = _charge_starts(seen["plan"])
    assert preview_starts == live_starts
    assert preview_starts == (T0 + 2 * SLOT,)

    _, _, zero_catch = recover_if_needed(
        plan, now, soc_pct=70.0, prices=prices, enabled=True,
        degradation_eur_per_kwh=0.0, risk_margin_eur_per_kwh=0.0, **SIZING,
    )
    zero_starts = _charge_starts(zero_catch.plan)
    assert zero_starts != live_starts
    assert len(zero_starts) == 2


CAP_EUR = 0.20


def _cap_prices() -> list[PriceSlot]:
    return [_price(i, PEAK_EUR if i == 8 else (0.15 if i == 2 else (0.25 if i == 3 else 0.90)))
            for i in range(10)]


def _live_recovery_with_max_buy(
    plan: Plan, prices: list[PriceSlot], now: datetime, max_buy_eur_per_kwh: float,
):
    return recover_if_needed(
        plan, now, soc_pct=50.0, prices=prices, enabled=True,
        degradation_eur_per_kwh=0.0, risk_margin_eur_per_kwh=0.0,
        max_buy_eur_per_kwh=max_buy_eur_per_kwh, **SIZING,
    )


def _preview_recovery(plan, prices, now, validate_fn, *, soc_pct=70.0, **kwargs):
    asyncio.run(_run_recovery(
        plan, now, soc_pct=soc_pct, prices=prices, enabled=True, tz=UTC,
        cache_store=None, notifier=None, audit_store=None, validate_fn=validate_fn,
        degradation_eur_per_kwh=kwargs.pop("degradation_eur_per_kwh", DEG),
        risk_margin_eur_per_kwh=kwargs.pop("risk_margin_eur_per_kwh", RISK),
        **SIZING, **kwargs,
    ))


def test_preview_recovery_fallback_respects_max_buy_eur_per_kwh_cap():
    """#414: preview fallback passes max_buy_eur_per_kwh=0.20 like the live planner."""
    plan = _missed_plan()
    prices = _cap_prices()
    now = T0
    _, _, live_catch = _live_recovery_with_max_buy(plan, prices, now, max_buy_eur_per_kwh=CAP_EUR)
    assert live_catch is not None
    live_starts = _charge_starts(live_catch.plan)
    _, _, uncapped_catch = _live_recovery_with_max_buy(
        plan, prices, now, max_buy_eur_per_kwh=0.0,
    )
    assert len(_charge_starts(uncapped_catch.plan)) > len(live_starts)
    assert any(
        p.eur_per_kwh > CAP_EUR for p in prices
        if p.start in _charge_starts(uncapped_catch.plan)
    )

    seen: dict = {}

    def _validator(catch_plan, _now):
        seen["plan"] = catch_plan
        return PlanValidation(status="valid")

    _preview_recovery(
        plan, prices, now, _validator, soc_pct=50.0,
        degradation_eur_per_kwh=0.0, risk_margin_eur_per_kwh=0.0,
        max_buy_eur_per_kwh=CAP_EUR,
    )
    preview_starts = _charge_starts(seen["plan"])
    assert preview_starts == live_starts
    assert not any(
        p.eur_per_kwh > CAP_EUR for p in prices if p.start in preview_starts
    )


def test_preview_recovery_identical_with_zero_max_buy_eur_per_kwh_no_cap():
    """#414: cap 0 or omitted max_buy_eur_per_kwh leaves preview unchanged."""
    plan = _missed_plan()
    prices = _prices()
    now = T0

    def _capture(seen: dict):
        def _validator(catch_plan, _now):
            seen["plan"] = catch_plan
            return PlanValidation(status="valid")
        return _validator

    seen_explicit: dict = {}
    _preview_recovery(plan, prices, now, _capture(seen_explicit), max_buy_eur_per_kwh=0.0)
    seen_omitted: dict = {}
    _preview_recovery(plan, prices, now, _capture(seen_omitted))
    assert _charge_starts(seen_explicit["plan"]) == _charge_starts(seen_omitted["plan"])

    _, _, baseline_catch = recover_if_needed(
        plan, now, soc_pct=70.0, prices=prices, enabled=True,
        degradation_eur_per_kwh=DEG, risk_margin_eur_per_kwh=RISK, **SIZING,
    )
    assert _charge_starts(seen_omitted["plan"]) == _charge_starts(baseline_catch.plan)
