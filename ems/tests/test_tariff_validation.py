from ems.tariff_validation import validate_tariff_policy
from ems.tariffs import TariffPolicy


def test_warns_when_post_2027_export_has_no_fee():
    warnings = validate_tariff_policy(TariffPolicy(), export_model="spot_minus_tax")
    assert warnings[0].code == "missing_export_fee"


def test_warns_when_net_metering_has_export_fee():
    warnings = validate_tariff_policy(
        TariffPolicy(export_fee_eur_per_kwh=0.02), export_model="net_metering")
    assert warnings[0].code == "export_fee_with_net_metering"
