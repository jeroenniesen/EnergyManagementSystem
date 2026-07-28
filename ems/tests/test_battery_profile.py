from ems.battery_profile import BatteryTopology, apply_topology_defaults, normalize_tower_ips


def test_normalize_tower_ips_supports_single_tower():
    assert normalize_tower_ips("192.0.2.20") == ("192.0.2.20",)


def test_normalize_tower_ips_deduplicates_and_preserves_master_first():
    assert normalize_tower_ips("192.0.2.20", " 192.0.2.21,192.0.2.20,, ") == (
        "192.0.2.20", "192.0.2.21"
    )


def test_topology_labels_single_and_multi_tower_configs():
    assert BatteryTopology(("a",)).tower_count == 1
    assert BatteryTopology(("a",)).label == "battery"
    assert BatteryTopology(("a", "b")).is_cluster
    assert BatteryTopology(("a", "b")).label == "battery cluster"


def test_single_tower_uses_safe_legacy_defaults():
    settings = {"battery.indevolt_ip": "a", "battery.indevolt_ips_extra": "",
                "battery.usable_kwh": 10.8, "battery.max_charge_w": 4000.0,
                "battery.max_discharge_w": 4000.0}
    result = apply_topology_defaults(settings)
    assert result["battery.usable_kwh"] == 5.4
    assert result["battery.max_charge_w"] == 2400.0
    assert result["battery.max_discharge_w"] == 2400.0


def test_explicit_single_tower_overrides_are_preserved():
    settings = {"battery.indevolt_ip": "a", "battery.indevolt_ips_extra": "",
                "battery.usable_kwh": 7.2, "battery.max_charge_w": 1800.0,
                "battery.max_discharge_w": 2200.0}
    assert apply_topology_defaults(settings) == settings


def test_legacy_defaults_scale_for_three_towers():
    settings = {"battery.indevolt_ip": "a", "battery.indevolt_ips_extra": "b,c",
                "battery.usable_kwh": 10.8, "battery.max_charge_w": 4000.0,
                "battery.max_discharge_w": 4000.0}
    result = apply_topology_defaults(settings)
    assert result["battery.usable_kwh"] == 16.2
    assert result["battery.max_charge_w"] == 7200.0
    assert result["battery.max_discharge_w"] == 7200.0


def test_legacy_two_tower_defaults_remain_compatible():
    settings = {"battery.indevolt_ip": "a", "battery.indevolt_ips_extra": "b",
                "battery.usable_kwh": 10.8, "battery.max_charge_w": 4000.0,
                "battery.max_discharge_w": 4000.0}
    result = apply_topology_defaults(settings)
    assert result["battery.usable_kwh"] == 10.8
    assert result["battery.max_charge_w"] == 4800.0
    assert result["battery.max_discharge_w"] == 4800.0
