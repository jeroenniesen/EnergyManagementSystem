"""Multi-tower Indevolt read: aggregate SoC (capacity-weighted) + summed power, per-tower detail,
partial-failure tolerance. No hardware — every client gets a stub POST (CLAUDE.md)."""
from ems.sources.indevolt import (
    BatteryUnavailable,
    IndevoltClusterReader,
    IndevoltReadClient,
    aggregate_soc,
    tower_mode_label,
)


def _client(ip: str, response: dict) -> IndevoltReadClient:
    # Stub POST ignores the requested keys and returns a fixed GetData dict.
    return IndevoltReadClient(ip, rpc_post=lambda _keys: response)


def test_capacity_weighted_soc_across_towers():
    # Bigger tower pulls the average toward its own SoC: (50*5 + 40*15)/20 = 42.5
    master = _client("a", {"6002": 50, "6000": 0, "6001": 1000, "606": 1000, "142": 5.0})
    slave = _client("b", {"6002": 40, "6000": 0, "6001": 1000, "606": 1001, "142": 15.0})
    power, soc = IndevoltClusterReader([master, slave]).read_power_soc()
    assert soc == 42.5
    assert power == 0.0


def test_tower_mode_label_distinguishes_self_consumption_from_standby():
    # The car-guard verification: a tower in self-consumption (7101=1) WILL feed the car; a tower in
    # real-time standby (7101=4, state static) won't. These must read differently.
    assert tower_mode_label(1, 1000) == "self-consumption"
    assert tower_mode_label(4, 1000) == "standby"
    assert tower_mode_label(4, 1002) == "discharging"
    assert tower_mode_label(4, 1001) == "charging"
    assert tower_mode_label(0, 1000) == "outdoor"
    assert tower_mode_label(None, None) is None


def test_per_tower_mode_is_read_and_exposed():
    # A cluster where the master went to standby but the slave is still self-consuming — exactly the
    # bug to surface. The per-tower mode must reflect each tower's ACTUAL working mode.
    master = _client("a", {"6002": 50, "6000": 0, "6001": 1000, "7101": 4, "606": 1000, "142": 5})
    slave = _client("b", {"6002": 50, "6000": 900, "6001": 1002, "7101": 1, "606": 1001, "142": 5})
    towers = IndevoltClusterReader([master, slave]).read_towers()
    assert towers[0].mode == "standby"  # master obeyed the idle command
    assert towers[1].mode == "self-consumption"  # slave did NOT — still discharging into the car


def test_power_is_signed_sum():
    a = _client("a", {"6002": 50, "6000": 1000, "6001": 1001, "142": 5})  # charging -> -1000
    b = _client("b", {"6002": 50, "6000": 400, "6001": 1002, "142": 5})  # discharging -> +400
    power, soc = IndevoltClusterReader([a, b]).read_power_soc()
    assert power == -600.0
    assert soc == 50.0


def test_per_tower_detail_with_roles():
    a = _client("a", {"6002": 49, "6000": 0, "6001": 1000, "606": 1000, "142": 5.38})
    b = _client("b", {"6002": 48, "6000": 0, "6001": 1000, "606": 1001, "142": 5.6})
    towers = IndevoltClusterReader([a, b]).read_towers()
    assert [t.role for t in towers] == ["master", "slave"]
    assert towers[0].capacity_kwh == 5.38
    assert towers[0].online is True
    assert towers[0].soc_pct == 49.0


def test_partial_failure_aggregates_online_only():
    def boom(_keys):
        raise OSError("tower unreachable")

    a = _client("a", {"6002": 60, "6000": 0, "6001": 1000, "142": 5})
    b = IndevoltReadClient("b", rpc_post=boom)
    reader = IndevoltClusterReader([a, b])
    power, soc = reader.read_power_soc()
    assert soc == 60.0  # only the reachable tower counts
    assert power == 0.0
    towers = reader.read_towers()
    assert [t.online for t in towers] == [True, False]


def test_all_towers_down_raises_battery_unavailable():
    def boom(_keys):
        raise OSError("down")

    reader = IndevoltClusterReader([IndevoltReadClient("a", rpc_post=boom)])
    try:
        reader.read_power_soc()
        raise AssertionError("expected BatteryUnavailable")
    except BatteryUnavailable:
        pass


def test_capacity_cached_and_reused_when_a_later_read_omits_it():
    seq = iter([
        {"6002": 50, "6000": 0, "6001": 1000, "142": 8.0},  # first read carries capacity
        {"6002": 52, "6000": 0, "6001": 1000},  # later read omits it
    ])
    client = IndevoltReadClient("a", rpc_post=lambda _keys: next(seq))
    reader = IndevoltClusterReader([client])
    reader.read_towers()  # caches 8.0
    later = reader.read_towers()[0]
    assert later.capacity_kwh == 8.0  # reused from cache


def test_simple_average_when_capacities_unknown():
    a = _client("a", {"6002": 30, "6000": 0, "6001": 1000})  # no 142 anywhere
    b = _client("b", {"6002": 50, "6000": 0, "6001": 1000})
    _power, soc = IndevoltClusterReader([a, b]).read_power_soc()
    assert soc == 40.0  # plain mean fallback


def test_role_register_arrives_as_string_from_the_device():
    # Real Gen-2 firmware returns key 606 as a STRING ("1000"), unlike the int state register.
    a = _client("a", {"6002": 49, "6000": 0, "6001": 1000, "606": "1000", "142": 5.38})
    b = _client("b", {"6002": 48, "6000": 0, "6001": 1000, "606": "1001", "142": 5.6})
    towers = IndevoltClusterReader([a, b]).read_towers()
    assert [t.role for t in towers] == ["master", "slave"]


def test_aggregate_soc_pure_helper():
    from ems.sources.indevolt import TowerReading

    r1 = TowerReading("a", 80.0, 0.0, 2.0, "master", True)
    r2 = TowerReading("b", 20.0, 0.0, 6.0, "slave", True)
    assert aggregate_soc([r1, r2]) == 35.0  # (80*2 + 20*6)/8


def test_aggregate_soc_ignores_socless_readings_and_guards_empty():
    import pytest

    from ems.sources.indevolt import TowerReading

    have = TowerReading("a", 80.0, 0.0, None, "master", True)
    none = TowerReading("b", None, 0.0, None, "slave", False)
    # A SoC-less reading must not dilute the mean toward zero (would give 40 if counted).
    assert aggregate_soc([have, none]) == 80.0
    with pytest.raises(ValueError):
        aggregate_soc([])
    with pytest.raises(ValueError):
        aggregate_soc([none])
