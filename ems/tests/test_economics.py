"""Shared energy economics (backlog B-05 / post-2027): the arbitrage break-even the planners spend
against, and the feed-in value of exported energy under net-metering (today) vs the post-saldering
models. Pure numeric identities — no I/O, no hardware."""
import pytest

from ems.planner.economics import (
    EXPORT_MODELS,
    apply_max_buy_cap,
    breakeven,
    export_value,
    max_buy_for_peak,
    max_buy_for_planner_config,
)
from ems.planner.rule_based import PlannerConfig


def test_breakeven_numeric_identity():
    # 0.20/0.9 + 0.05 + 0.02 = 0.2222… + 0.07 = 0.2922…  (the single source of truth both planners
    # now call — this is the exact formula they used inline before the refactor).
    be = breakeven(0.20, round_trip_efficiency=0.90,
                   degradation_eur_per_kwh=0.05, risk_margin_eur_per_kwh=0.02)
    assert abs(be - (0.20 / 0.9 + 0.05 + 0.02)) < 1e-12


def test_max_buy_for_peak_matches_legacy_winter_formula():
    """#327 AC1: the helper is the old winter ceiling, including the acceptance numbers."""
    import json
    from pathlib import Path

    # (0.40 - 0.03 - 0.01) * 0.85 = 0.306. Same operations the winter planner inlined.
    assert max_buy_for_peak(0.40, rte=0.85, deg=0.03, risk=0.01) == (0.40 - 0.03 - 0.01) * 0.85
    cases = json.loads(
        (Path(__file__).parent / "fixtures" / "max_buy_for_peak.json").read_text(encoding="utf-8")
    )["cases"]
    assert len(cases) == 5
    for case in cases:
        legacy = (case["peak"] - case["deg"] - case["risk"]) * case["rte"]
        got = max_buy_for_peak(case["peak"], rte=case["rte"], deg=case["deg"], risk=case["risk"])
        assert got == legacy
        assert got == pytest.approx(case["expected"], abs=1e-12)
        cfg = PlannerConfig(
            round_trip_efficiency=case["rte"],
            degradation_eur_per_kwh=case["deg"],
            risk_margin_eur_per_kwh=case["risk"],
        )
        assert max_buy_for_planner_config(case["peak"], cfg) == got


def test_apply_max_buy_cap_resident_ceiling():
    assert apply_max_buy_cap(0.297, 0.0) == 0.297
    assert apply_max_buy_cap(0.297, 0.20) == 0.20


def test_export_models_tuple():
    assert EXPORT_MODELS == ("net_metering", "spot_minus_tax", "fixed")


def test_export_value_net_metering_is_full_price():
    # Today's saldering: an exported kWh nets against import at the full retail price.
    assert export_value(0.20) == 0.20
    assert export_value(0.20, model="net_metering") == 0.20


def test_export_value_spot_minus_tax():
    # Post-2027 dynamic export: spot minus the energy tax. 0.20 − 0.13 = 0.07.
    v = export_value(0.20, model="spot_minus_tax", energy_tax_eur_per_kwh=0.13)
    assert abs(v - 0.07) < 1e-12


def test_export_value_fixed_ignores_spot():
    # A flat feed-in tariff, independent of the spot price.
    assert export_value(0.20, model="fixed", fixed_feed_in_eur_per_kwh=0.01) == 0.01
    assert export_value(-0.50, model="fixed", fixed_feed_in_eur_per_kwh=0.01) == 0.01


def test_export_value_spot_minus_tax_can_go_negative():
    # Negative-spot slot: −0.02 − 0.13 = −0.15. Exporting COSTS money and must NOT be clamped to 0.
    v = export_value(-0.02, model="spot_minus_tax", energy_tax_eur_per_kwh=0.13)
    assert abs(v - (-0.15)) < 1e-12


def test_export_value_unknown_model_falls_back_to_net_metering():
    # Fail-safe: never raise in the hot path — an unknown model behaves like net-metering.
    assert export_value(0.20, model="does_not_exist") == 0.20
