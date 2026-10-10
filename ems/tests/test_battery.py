from ems.domain import BatteryIntent, PhysicalMode
from ems.sources.battery import (
    FailingMockBatteryDriver,
    MockBatteryDriver,
    intent_to_mode,
)


def test_discharge_for_load_mapping_is_always_auto_except_car_session():
    """DISCHARGE_FOR_LOAD → AUTO; allow_export_discharge does not promote it (E-11 / B-105)."""
    cases = (
        (False, False, PhysicalMode.AUTO),
        (True, False, PhysicalMode.AUTO),
        (False, True, PhysicalMode.DISCHARGE),
        (True, True, PhysicalMode.DISCHARGE),
    )
    for allow_export, car_session, expected in cases:
        assert (
            intent_to_mode(
                BatteryIntent.DISCHARGE_FOR_LOAD,
                allow_export_discharge=allow_export,
                car_session=car_session,
            )
            is expected
        )


def test_export_for_profit_requires_arming():
    assert intent_to_mode(BatteryIntent.EXPORT_FOR_PROFIT) is PhysicalMode.AUTO
    assert (
        intent_to_mode(BatteryIntent.EXPORT_FOR_PROFIT, allow_export_discharge=True)
        is PhysicalMode.DISCHARGE
    )


def test_intent_to_mode_covers_all_intents():
    assert intent_to_mode(BatteryIntent.ALLOW_SELF_CONSUMPTION) is PhysicalMode.AUTO
    assert intent_to_mode(BatteryIntent.GRID_CHARGE_TO_TARGET) is PhysicalMode.CHARGE
    assert intent_to_mode(BatteryIntent.HOLD_RESERVE) is PhysicalMode.IDLE
    assert intent_to_mode(BatteryIntent.DISCHARGE_FOR_LOAD) is PhysicalMode.AUTO
    assert (
        intent_to_mode(BatteryIntent.DISCHARGE_FOR_LOAD, allow_export_discharge=True)
        is PhysicalMode.AUTO
    )
    assert intent_to_mode(BatteryIntent.EXPORT_FOR_PROFIT) is PhysicalMode.AUTO
    # every intent maps to something
    assert {intent_to_mode(i) for i in BatteryIntent} <= set(PhysicalMode)


def test_failing_driver_reports_unconfirmed_then_succeeds():
    d = FailingMockBatteryDriver(fail_times=1)
    assert d.apply(PhysicalMode.CHARGE) is False  # unconfirmed
    assert d.current_mode() is PhysicalMode.AUTO  # mode unchanged on failed apply
    assert d.apply(PhysicalMode.CHARGE) is True  # second attempt confirms
    assert d.current_mode() is PhysicalMode.CHARGE


def test_probe_returns_capabilities():
    cap = MockBatteryDriver().probe()
    assert "charge" in cap.services and "discharge" in cap.services
    assert cap.p1_paired is True
    assert cap.max_charge_w == 4000.0
    # #112 slice b — vendor-neutral floors/flags (Fake Indevolt OpenData envelope).
    assert cap.supports_discharge_control is True
    assert cap.supports_grid_charge is True
    assert cap.supports_standby is True
    assert cap.min_power_w == 50.0
    assert cap.min_target_soc == 5.0


def test_configure_power_limits_preserves_vendor_neutral_floors():
    d = MockBatteryDriver()
    d.configure_power_limits(max_charge_w=2000.0, max_discharge_w=1800.0)
    cap = d.probe()
    assert cap.max_charge_w == 2000.0 and cap.max_discharge_w == 1800.0
    assert cap.min_power_w == 50.0 and cap.min_target_soc == 5.0
    assert cap.supports_grid_charge is True


def test_apply_changes_mode_and_is_idempotent():
    d = MockBatteryDriver()
    assert d.current_mode() is PhysicalMode.AUTO
    assert d.apply(PhysicalMode.CHARGE) is True
    assert d.current_mode() is PhysicalMode.CHARGE
    assert d.apply(PhysicalMode.CHARGE) is True  # idempotent re-apply still confirms
    assert d.current_mode() is PhysicalMode.CHARGE
