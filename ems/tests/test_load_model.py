import pytest

from ems.domain import RawSample
from ems.load_model import (
    MAX_BATTERY_W,
    MAX_SOLAR_W,
    DerivedSample,
    assess_reconstruction,
    is_soc_jump_implausible,
    normalise_solar,
    reconstruct,
    sanitize_sample,
)


def _raw(grid, solar, batt, ev=0.0, soc=50.0):
    return RawSample(
        grid_power_w=grid,
        solar_power_w=solar,
        battery_power_w=batt,
        ev_power_w=ev,
        soc_pct=soc,
    )


@pytest.mark.parametrize(
    "grid,solar,batt,ev,expected_house,expected_non_ev",
    [
        (1000, 0, 0, 0, 1000, 1000),  # grid-only
        (-500, 1500, 0, 0, 1000, 1000),  # solar covers + export
        (200, 0, 800, 0, 1000, 1000),  # battery covers
        (1500, 0, -500, 0, 1000, 1000),  # charging from grid
        (200, 1500, 0, 700, 1700, 1000),  # solar + EV charging
    ],
)
def test_reconstruction_consistency_cases(grid, solar, batt, ev, expected_house, expected_non_ev):
    d = reconstruct(_raw(grid, solar, batt, ev))
    assert d == DerivedSample(house_load_w=expected_house, non_ev_load_w=expected_non_ev)


def test_ev_not_subtracted_below_threshold():
    # EV drawing 100 W (< 200 threshold) is treated as not charging -> not subtracted
    d = reconstruct(_raw(300, 0, 0, ev=100), ev_charging_threshold_w=200.0)
    assert d.house_load_w == 300
    assert d.non_ev_load_w == 300


def test_materially_negative_reconstruction_is_not_valid_for_learning():
    assessment = assess_reconstruction(_raw(-2000, 500, 0))
    assert assessment.valid_for_learning is False
    assert "negative_house_load" in assessment.flags


def test_small_negative_noise_is_clamped_to_zero_for_learning():
    assessment = assess_reconstruction(_raw(-20, 0, 0))
    assert assessment.valid_for_learning is True
    assert assessment.derived.house_load_w == 0.0
    assert "clamped_noise" in assessment.flags


def test_valid_solar_export_is_not_quarantined():
    assessment = assess_reconstruction(_raw(-1000, 1500, 0))
    assert assessment.valid_for_learning is True
    assert assessment.derived.house_load_w == 500.0


def test_ev_larger_than_reconstructed_load_is_not_valid_for_learning():
    assessment = assess_reconstruction(_raw(500, 0, 0, ev=1000))
    assert assessment.valid_for_learning is False
    assert "negative_non_ev_load" in assessment.flags


def test_normalise_solar_clamps_negative():
    assert normalise_solar(-5.0) == 0.0
    assert normalise_solar(1234.0) == 1234.0


def test_soc_jump_plausibility():
    assert is_soc_jump_implausible(50.0, 69.0, 5.0) is False  # 19% in 5 min, ok
    assert is_soc_jump_implausible(50.0, 75.0, 5.0) is True  # 25% in 5 min, implausible
    assert is_soc_jump_implausible(None, 80.0, 5.0) is False  # no prior reading


def test_sanitize_sample_clamps_battery_overrange():
    raw = _raw(200, 0, 50000.0)  # gross garbage, way past the ~4-5 kW inverter
    corrected, clamped = sanitize_sample(raw)
    assert corrected.battery_power_w == MAX_BATTERY_W
    assert clamped == ("battery",)


def test_sanitize_sample_clamps_battery_underrange():
    raw = _raw(200, 0, -50000.0)
    corrected, clamped = sanitize_sample(raw)
    assert corrected.battery_power_w == -MAX_BATTERY_W
    assert clamped == ("battery",)


def test_sanitize_sample_clamps_solar_overrange():
    raw = _raw(200, 40000.0, 800)
    corrected, clamped = sanitize_sample(raw)
    assert corrected.solar_power_w == MAX_SOLAR_W
    assert clamped == ("solar",)


def test_sanitize_sample_clamps_negative_solar():
    raw = _raw(200, -100.0, 800)
    corrected, clamped = sanitize_sample(raw)
    assert corrected.solar_power_w == 0.0
    assert clamped == ("solar",)


def test_sanitize_sample_leaves_normal_sample_unchanged():
    raw = _raw(200, 2600.0, 4200.0)
    corrected, clamped = sanitize_sample(raw)
    assert corrected is raw  # same object, byte-for-byte unchanged
    assert clamped == ()
