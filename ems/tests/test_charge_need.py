from ems.planner.charge_need import (
    charge_need_from_target,
    compute_charge_need,
    ui_charge_need,
)


def _need(soc, **kw):
    base = dict(
        soc_pct=soc, usable_kwh=10.0, min_reserve_soc=10.0,
        night_reserve_kwh=2.0, overnight_load_kwh=5.0,
    )
    base.update(kw)
    return compute_charge_need(**base)


def test_deficit_when_soc_low():
    n = _need(20.0)  # 2.0 kWh stored; target = 5 + 2 + 1 = 8 kWh
    assert n.target_kwh == 8.0
    assert n.current_kwh == 2.0
    assert n.deficit_kwh == 6.0
    assert n.on_track is False
    assert "Need" in n.reason


def test_on_track_when_soc_high():
    n = _need(90.0)  # 9.0 kWh stored >= 8 kWh target
    assert n.deficit_kwh == 0.0
    assert n.on_track is True
    assert "On track" in n.reason


def test_target_capped_at_usable_capacity():
    # Demand far exceeds capacity -> target clamps to usable, target SoC never exceeds 100%.
    n = _need(50.0, overnight_load_kwh=40.0, night_reserve_kwh=10.0)
    assert n.target_kwh == 10.0
    assert n.target_soc_pct == 100.0


def test_reserve_floor_scales_with_capacity():
    n = _need(50.0, usable_kwh=20.0, min_reserve_soc=15.0)
    assert n.reserve_kwh == 3.0  # 15% of 20 kWh


def test_zero_capacity_is_safe():
    n = _need(50.0, usable_kwh=0.0)
    assert n.on_track is True
    assert n.target_kwh == 0.0
    assert "not configured" in n.reason


def test_soc_is_clamped():
    assert _need(150.0).current_soc_pct == 100.0
    assert _need(-10.0).current_soc_pct == 0.0


def test_efficiency_raises_target_to_cover_round_trip_losses():
    # overnight load + night reserve are AC energy to DELIVER; at 81% round-trip (eta = 0.9) the
    # pack must hold (5 + 2) / 0.9 = 7.78 kWh DC, plus the 1 kWh reserve floor = 8.78 kWh.
    n = _need(50.0, round_trip_efficiency=0.81)
    assert round(n.target_kwh, 2) == 8.78
    # The default efficiency (1.0) leaves the plain sum unchanged (back-compatible with callers).
    assert _need(50.0).target_kwh == 8.0


def test_charge_need_from_committed_target_ignores_advisory_ceiling():
    # #180: honest-partial plan target (e.g. 29%) must not re-inflate via overnight config (~80%).
    n = charge_need_from_target(
        soc_pct=10.0, usable_kwh=10.0, min_reserve_soc=10.0, target_soc_pct=29.0,
    )
    assert n.target_soc_pct == 29.0
    assert n.target_kwh == 2.9
    assert round(n.deficit_kwh, 1) == 1.9
    assert n.on_track is False
    assert "plan target" in n.reason


def test_charge_need_from_target_on_track_when_soc_meets_commitment():
    n = charge_need_from_target(
        soc_pct=30.0, usable_kwh=10.0, min_reserve_soc=10.0, target_soc_pct=29.0,
    )
    assert n.on_track is True
    assert n.deficit_kwh == 0.0


def test_ui_charge_need_prefers_plan_target_over_advisory():
    # Default overnight formula with these inputs → 80% advisory; plan commits 25%.
    advisory = ui_charge_need(
        soc_pct=5.0, usable_kwh=10.0, min_reserve_soc=10.0,
        night_reserve_kwh=2.0, overnight_load_kwh=5.0, plan_target_soc=None,
    )
    assert advisory.target_soc_pct == 80.0
    committed = ui_charge_need(
        soc_pct=5.0, usable_kwh=10.0, min_reserve_soc=10.0,
        night_reserve_kwh=2.0, overnight_load_kwh=5.0, plan_target_soc=25.0,
    )
    assert committed.target_soc_pct == 25.0
    assert committed.deficit_kwh == 2.0
