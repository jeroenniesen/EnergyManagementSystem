from ems.domain import BatteryIntent, PhysicalMode
from ems.sources.battery import MockBatteryDriver, intent_to_mode


def test_intent_to_mode_covers_all_intents():
    assert intent_to_mode(BatteryIntent.ALLOW_SELF_CONSUMPTION) is PhysicalMode.AUTO
    assert intent_to_mode(BatteryIntent.GRID_CHARGE_TO_TARGET) is PhysicalMode.CHARGE
    assert intent_to_mode(BatteryIntent.HOLD_RESERVE) is PhysicalMode.IDLE
    assert intent_to_mode(BatteryIntent.DISCHARGE_FOR_LOAD) is PhysicalMode.DISCHARGE
    # every intent maps to something
    assert {intent_to_mode(i) for i in BatteryIntent} <= set(PhysicalMode)


def test_probe_returns_capabilities():
    cap = MockBatteryDriver().probe()
    assert "charge" in cap.services and "discharge" in cap.services
    assert cap.p1_paired is True
    assert cap.max_charge_w == 4000.0


def test_apply_changes_mode_and_is_idempotent():
    d = MockBatteryDriver()
    assert d.current_mode() is PhysicalMode.AUTO
    assert d.apply(PhysicalMode.CHARGE) is True
    assert d.current_mode() is PhysicalMode.CHARGE
    assert d.apply(PhysicalMode.CHARGE) is True  # idempotent re-apply still confirms
    assert d.current_mode() is PhysicalMode.CHARGE
