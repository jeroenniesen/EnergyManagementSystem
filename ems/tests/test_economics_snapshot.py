import pytest

from ems.economics import EconomicSnapshot
from ems.planner.economics import breakeven, export_value
from ems.tariffs import TariffPolicy


@pytest.mark.parametrize(
    "model,price,expected",
    [
        ("net_metering", 0.20, 0.20),
        ("spot_minus_tax", 0.20, 0.07),
        ("spot_minus_tax", -0.02, -0.15),
        ("fixed", -0.20, 0.01),
        ("unknown", 0.20, 0.20),
    ],
)
def test_export_credit_matches_existing_formula(model, price, expected):
    snap = EconomicSnapshot.from_tariff_policy(
        TariffPolicy(), raw_price_eur_per_kwh=price, export_model=model
    )
    assert snap.export_credit(price) == pytest.approx(export_value(price, model=model))
    assert snap.export_credit(price) == pytest.approx(expected)


def test_delivered_cost_and_break_even_match_existing_formula():
    snap = EconomicSnapshot.from_tariff_policy(
        TariffPolicy(import_fee_eur_per_kwh=0.03),
        raw_price_eur_per_kwh=0.10,
        round_trip_efficiency=0.9,
        degradation_eur_per_kwh=0.05,
        risk_margin_eur_per_kwh=0.02,
    )
    assert snap.delivered_energy_cost() == pytest.approx((0.10 + 0.03) / 0.9 + 0.05 + 0.02)
    assert snap.break_even_import_price(0.40) == pytest.approx((0.40 - 0.05 - 0.02) * 0.9)
    assert breakeven(
        0.10, round_trip_efficiency=0.9, degradation_eur_per_kwh=0.05, risk_margin_eur_per_kwh=0.02
    ) == pytest.approx(0.10 / 0.9 + 0.05 + 0.02)


def test_policy_fees_and_metadata_are_serializable():
    snap = EconomicSnapshot.from_tariff_policy(
        TariffPolicy(
            tibber_total_includes_all=False,
            import_fee_eur_per_kwh=0.04,
            export_fee_eur_per_kwh=0.01,
        ),
        raw_price_eur_per_kwh=0.20,
    )
    assert snap.import_price_eur_per_kwh == pytest.approx(0.24)
    assert snap.export_price_eur_per_kwh == pytest.approx(0.19)
    assert snap.metadata()["import_fee_eur_per_kwh"] == 0.04
    with pytest.raises((AttributeError, TypeError)):
        snap.import_price_eur_per_kwh = 1.0


@pytest.mark.parametrize(
    "model,expected", [("net_metering", 0.18), ("spot_minus_tax", 0.05), ("fixed", -0.01)]
)
def test_export_fee_matches_finance_for_every_model(model, expected):
    snap = EconomicSnapshot.from_tariff_policy(
        TariffPolicy(export_fee_eur_per_kwh=0.02),
        raw_price_eur_per_kwh=0.20,
        export_model=model,
    )
    assert snap.export_credit(0.20) == pytest.approx(expected)


def test_nonfinite_inputs_are_rejected():
    with pytest.raises(ValueError):
        EconomicSnapshot(import_price_eur_per_kwh=float("nan"), export_price_eur_per_kwh=0.0)
    with pytest.raises(ValueError):
        EconomicSnapshot.from_tariff_policy(raw_price_eur_per_kwh=float("inf"))
    snap = EconomicSnapshot.from_tariff_policy()
    with pytest.raises(ValueError):
        snap.delivered_energy_cost(float("nan"))
    with pytest.raises(ValueError):
        snap.export_credit(float("inf"))


@pytest.mark.parametrize("efficiency", [-1.0, 0.0, 2.0])
def test_efficiency_is_clamped_safely(efficiency):
    snap = EconomicSnapshot.from_tariff_policy(round_trip_efficiency=efficiency)
    assert snap.delivered_energy_cost(0.2) >= 0.2
