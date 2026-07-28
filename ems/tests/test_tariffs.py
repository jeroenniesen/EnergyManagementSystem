import pytest

from ems.tariffs import TariffPolicy, policy_from_settings


def test_zero_fee_policy_is_backward_compatible():
    value = TariffPolicy().normalize(0.25)
    assert value.import_eur_per_kwh == 0.25
    assert value.export_eur_per_kwh == 0.25


def test_import_fee_only_applies_when_provider_total_is_incomplete():
    assert TariffPolicy(import_fee_eur_per_kwh=0.13).normalize(0.25).import_eur_per_kwh == 0.38
    assert TariffPolicy(True, 0.13).normalize(0.25).import_eur_per_kwh == 0.25


def test_export_fee_reduces_export_value_and_preserves_negative_values():
    value = TariffPolicy(export_fee_eur_per_kwh=0.05).normalize(-0.02)
    assert value.export_eur_per_kwh == pytest.approx(-0.07)
    value = TariffPolicy(export_fee_eur_per_kwh=0.05).normalize(0.20)
    assert value.export_eur_per_kwh == pytest.approx(0.15)


def test_policy_from_settings_clamps_invalid_negative_fees():
    policy = policy_from_settings({
        "grid_fees.import_fee_eur_per_kwh": -1,
        "grid_fees.export_fee_eur_per_kwh": "bad",
    })
    assert policy.import_fee_eur_per_kwh == 0.0
    assert policy.export_fee_eur_per_kwh == 0.0
