"""Plan validator (SPEC §8.11): unsafe ⇒ control-blocking (hold AUTO); warn ⇒ degraded/usable."""
from datetime import UTC, datetime, timedelta

from ems.domain import BatteryIntent, CapabilityReport
from ems.planner.projection import ProjectedSlot
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.validator import validate_plan

T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
CAP = CapabilityReport(services=("charge", "discharge"), energy_mode_options=(),
                       has_standby=True, has_grid_charge_switch=True, p1_paired=True,
                       max_charge_w=4000.0, max_discharge_w=4000.0)


def _plan(*slots: PlanSlot) -> Plan:
    return Plan(created_at=T0, slots=tuple(slots), strategy="summer")


def _self(i: int) -> PlanSlot:
    return PlanSlot(T0 + i * SLOT, BatteryIntent.ALLOW_SELF_CONSUMPTION, "self")


def _charge(i: int, *, target_soc=80.0, floor=10.0, power=4000.0) -> PlanSlot:
    return PlanSlot(T0 + i * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge",
                    target_soc=target_soc, floor_soc=floor, power_w=power)


def _ctx(**kw):
    base = dict(soc_pct=50.0, data_quality="complete", min_reserve_soc=10.0, capability=CAP)
    base.update(kw)
    return base


def test_clean_plan_is_valid():
    v = validate_plan(_plan(_charge(0), _self(1), _self(2)), **_ctx())
    assert v.status == "valid" and v.ok is True and v.findings == ()


def test_unsafe_data_quality_blocks_control():
    v = validate_plan(_plan(_charge(0)), **_ctx(data_quality="unsafe"))
    assert not v.ok
    assert any(f.code == "stale_inputs" for f in v.findings)


def test_unknown_soc_is_unsafe_stale_inputs():
    """#134: soc_pct=None must block control — never invent 0% for the validator."""
    v = validate_plan(_plan(_charge(0)), **_ctx(soc_pct=None, data_quality="complete"))
    assert not v.ok
    assert any(f.code == "stale_inputs" for f in v.findings)


def test_charge_target_above_100_is_unsafe():
    v = validate_plan(_plan(_charge(0, target_soc=130.0)), **_ctx())
    assert v.status == "unsafe" and any(f.code == "target_out_of_range" for f in v.findings)


def test_charge_target_below_reserve_is_unsafe():
    v = validate_plan(_plan(_charge(0, target_soc=5.0, floor=10.0)), **_ctx())
    assert v.status == "unsafe" and any(f.code == "target_below_reserve" for f in v.findings)


def test_unsized_charge_target_is_a_warning_not_blocking():
    # Winter charge slots don't carry a target yet (sized in Polish 2) — warn, but still applicable.
    v = validate_plan(_plan(_charge(0, target_soc=None)), **_ctx())
    assert v.status == "warn" and v.ok is True
    assert any(f.code == "charge_target_unsized" for f in v.findings)


def test_power_above_capability_pauses():
    """#85: known power exceedance is control-blocking (pause), not a soft warn."""
    v = validate_plan(_plan(_charge(0, power=9000.0)), **_ctx())
    assert any(f.code == "power_exceeds_capability" for f in v.findings)
    assert v.ok is False and v.status == "unsafe"


def test_unknown_capability_clamps_to_one_unit():
    """#85 criterion 6: no capability probe → cautious one-unit clamp, plan still applies."""
    from ems.planner.validator import clamp_plan_power

    plan = _plan(_charge(0, power=4800.0))
    aligned, findings = clamp_plan_power(plan, capability=None)
    assert aligned.slots[0].power_w == 2400.0
    assert any(f.code == "capability_unknown_conservative" for f in findings)
    v = validate_plan(aligned, **_ctx(capability=None))
    assert v.ok is True
    assert not any(f.code == "power_exceeds_capability" for f in v.findings)


def test_unknown_capability_fills_unsized_charge_slot():
    """#85 criterion 6: None power_w must not fall through to full settings default."""
    from ems.planner.validator import clamp_plan_power

    plan = _plan(_charge(0, power=None))
    aligned, findings = clamp_plan_power(plan, capability=None)
    assert aligned.slots[0].power_w == 2400.0
    assert any(f.code == "capability_unknown_conservative" for f in findings)


def test_power_exceeds_names_settings_vs_capability_divergence():
    """#164: when settings and capability disagree, the finding names both figures."""
    under = CapabilityReport(
        services=("charge", "discharge"), energy_mode_options=(),
        has_standby=True, has_grid_charge_switch=True, p1_paired=True,
        max_charge_w=2400.0, max_discharge_w=2400.0,
    )
    v = validate_plan(
        _plan(_charge(0, power=4800.0)),
        **_ctx(capability=under),
        settings_max_charge_w=4800.0,
    )
    f = next(f for f in v.findings if f.code == "power_exceeds_capability")
    assert "4800" in f.message and "2400" in f.message
    assert "Settings advertise" in f.message
    assert f.severity == "unsafe" and v.ok is False


def test_clamp_plan_power_before_validate_avoids_reject_path():
    """#164: clamp to min(settings, capability) so a 4800 W catch-up slot survives validate."""
    from ems.planner.validator import clamp_plan_power

    under = CapabilityReport(
        services=("charge", "discharge"), energy_mode_options=(),
        has_standby=True, has_grid_charge_switch=True, p1_paired=True,
        max_charge_w=2400.0, max_discharge_w=2400.0,
    )
    plan = _plan(_charge(0, power=4800.0))
    aligned, clamp_findings = clamp_plan_power(
        plan, capability=under, settings_max_charge_w=4800.0, settings_max_discharge_w=4800.0,
    )
    assert aligned.slots[0].power_w == 2400.0
    assert any(f.code == "settings_capability_power_mismatch" for f in clamp_findings)
    assert any(f.code == "power_clamped_to_capability" for f in clamp_findings)
    v = validate_plan(
        aligned, **_ctx(capability=under),
        settings_max_charge_w=4800.0, settings_max_discharge_w=4800.0,
    )
    assert not any(f.code == "power_exceeds_capability" for f in v.findings)
    assert v.ok is True


def test_trusted_capability_overask_is_not_clamped_so_validate_can_pause():
    """#85 criterion 5: when settings ≤ capability, leave over-ask for unsafe pause."""
    from ems.planner.validator import clamp_plan_power

    trusted = CapabilityReport(
        services=("charge", "discharge"), energy_mode_options=(),
        has_standby=True, has_grid_charge_switch=True, p1_paired=True,
        max_charge_w=4000.0, max_discharge_w=4000.0,
    )
    plan = _plan(_charge(0, power=9000.0))
    aligned, clamp_findings = clamp_plan_power(
        plan, capability=trusted, settings_max_charge_w=4000.0, settings_max_discharge_w=4000.0,
    )
    assert aligned.slots[0].power_w == 9000.0
    assert not any(f.code == "power_clamped_to_capability" for f in clamp_findings)
    v = validate_plan(
        aligned, **_ctx(capability=trusted),
        settings_max_charge_w=4000.0, settings_max_discharge_w=4000.0,
    )
    assert any(f.code == "power_exceeds_capability" for f in v.findings)
    assert v.ok is False


def test_clamp_uses_higher_capability_when_gen2_exceeds_settings_floor():
    """#164: capability-driven clamp must not hardcode 2400 when capability is higher."""
    from ems.planner.validator import clamp_plan_power, effective_power_limit_w

    assert effective_power_limit_w(capability_w=6000.0, settings_w=5000.0) == 5000.0
    assert effective_power_limit_w(capability_w=6000.0, settings_w=7000.0) == 6000.0
    gen2 = CapabilityReport(
        services=("charge", "discharge"), energy_mode_options=(),
        has_standby=True, has_grid_charge_switch=True, p1_paired=True,
        max_charge_w=6000.0, max_discharge_w=6000.0,
    )
    plan = _plan(_charge(0, power=7000.0))
    aligned, _ = clamp_plan_power(
        plan, capability=gen2, settings_max_charge_w=7000.0, settings_max_discharge_w=7000.0,
    )
    assert aligned.slots[0].power_w == 6000.0


def test_target_below_capability_min_soc_is_unsafe():
    """#112 slice b: charge target under device min_target_soc is control-blocking."""
    from dataclasses import replace

    cap = replace(CAP, min_target_soc=5.0)
    v = validate_plan(
        _plan(_charge(0, target_soc=3.0, floor=0.0)),
        **_ctx(capability=cap, min_reserve_soc=0.0),
    )
    assert v.ok is False
    assert any(f.code == "target_below_capability_min" for f in v.findings)
    ok = validate_plan(
        _plan(_charge(0, target_soc=5.0, floor=0.0)),
        **_ctx(capability=cap, min_reserve_soc=0.0),
    )
    assert not any(f.code == "target_below_capability_min" for f in ok.findings)


def test_projection_below_reserve_is_unsafe():
    proj = [ProjectedSlot(T0, BatteryIntent.DISCHARGE_FOR_LOAD, 5.0, 0, 0, 0, 0)]
    v = validate_plan(_plan(_self(0)), projection=proj, **_ctx())
    assert v.status == "unsafe" and any(f.code == "projection_below_reserve" for f in v.findings)


def test_projection_already_below_reserve_without_further_drop_is_not_unsafe():
    proj = [
        ProjectedSlot(T0, BatteryIntent.HOLD_RESERVE, 8.0, 0, 0, 0, 800),
        ProjectedSlot(T0 + SLOT, BatteryIntent.HOLD_RESERVE, 8.0, 0, 0, 0, 800),
    ]
    v = validate_plan(_plan(_self(0), _self(1)), projection=proj, **_ctx(soc_pct=8.0))
    assert not any(f.code == "projection_below_reserve" for f in v.findings)
    assert v.ok is True


def test_excessive_switches_warns():
    # Alternate every slot → many transitions, above a tiny budget.
    slots = [(_charge(i) if i % 2 else _self(i)) for i in range(12)]
    v = validate_plan(_plan(*slots), **_ctx(), max_switches_per_day=3,
                      min_dwell=timedelta(seconds=1))
    assert any(f.code == "excessive_switches" for f in v.findings) and v.ok is True


def test_sub_dwell_churn_warns():
    slots = [_self(0), _charge(1), _self(2)]  # changes every 15 min
    v = validate_plan(_plan(*slots), **_ctx(), min_dwell=timedelta(minutes=30))
    assert any(f.code == "dwell_churn" for f in v.findings)


# --- Projected-target reachability gate (SPEC §8.5 later-step / BACKLOG B-22) ---------------------
def _charge_plan(target: float, deadline_slot: int) -> Plan:
    """A grid-charge plan carrying a plan-level target SoC + deadline (the seam the gate reads)."""
    slots = tuple(_charge(i, target_soc=target) for i in range(4))
    return Plan(created_at=T0, slots=slots, strategy="winter",
                target_soc=target, deadline=T0 + deadline_slot * SLOT)


def _proj(*soc_by_slot: float) -> list[ProjectedSlot]:
    return [ProjectedSlot(T0 + i * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, soc, 0, 0, 0, 0)
            for i, soc in enumerate(soc_by_slot)]


def test_projection_short_of_target_is_warn_with_the_numbers():
    # Plan commits to 88% by slot 4, but the projection tops out at 71% → clear (>5pp) shortfall.
    # #162: warn (not unsafe) so control keeps best-effort charging instead of failing to AUTO.
    plan = _charge_plan(88.0, deadline_slot=4)
    proj = _proj(40.0, 55.0, 65.0, 71.0, 71.0)
    v = validate_plan(plan, projection=proj, **_ctx())
    assert v.status == "warn"
    assert v.ok is True  # warn is still applicable — not control-blocking
    f = next(f for f in v.findings if f.code == "projection_short_of_target")
    assert f.severity == "warn"
    assert "88%" in f.message and "71%" in f.message


def test_projection_short_of_target_still_charges_when_below_reserve_start():
    # Battery already at/below reserve: short-of-target must NOT become the second death-spiral
    # (unsafe → AUTO → no charge). Reserve breach is a separate finding; short-of-target is warn.
    plan = _charge_plan(88.0, deadline_slot=4)
    proj = _proj(10.0, 25.0, 40.0, 55.0, 55.0)
    v = validate_plan(plan, projection=proj, **_ctx(soc_pct=5.0, min_reserve_soc=10.0))
    short = next(f for f in v.findings if f.code == "projection_short_of_target")
    assert short.severity == "warn"
    assert v.ok is True
    assert not any(f.code == "projection_below_reserve" for f in v.findings)


def test_projection_within_margin_passes():
    # 84% projected vs an 88% target = 4pp short, inside the 5pp margin → not a violation.
    plan = _charge_plan(88.0, deadline_slot=4)
    proj = _proj(40.0, 60.0, 75.0, 84.0, 84.0)
    v = validate_plan(plan, projection=proj, **_ctx())
    assert not any(f.code == "projection_short_of_target" for f in v.findings)
    assert v.ok is True


def test_projection_reaches_target_passes():
    plan = _charge_plan(88.0, deadline_slot=4)
    proj = _proj(40.0, 62.0, 80.0, 90.0, 90.0)
    v = validate_plan(plan, projection=proj, **_ctx())
    assert not any(f.code == "projection_short_of_target" for f in v.findings)


def test_projection_gate_skipped_when_data_degraded():
    # A short projection must NOT reject the plan when inputs are degraded — that's the data
    # fail-safe's call, not this gate's (don't reject a plan because the forecast is missing).
    plan = _charge_plan(88.0, deadline_slot=4)
    proj = _proj(40.0, 55.0, 65.0, 71.0, 71.0)
    v = validate_plan(plan, projection=proj, **_ctx(data_quality="degraded"))
    assert not any(f.code == "projection_short_of_target" for f in v.findings)


def test_projection_gate_can_be_disabled():
    plan = _charge_plan(88.0, deadline_slot=4)
    proj = _proj(40.0, 55.0, 65.0, 71.0, 71.0)
    v = validate_plan(plan, projection=proj, validate_projection=False, **_ctx())
    assert not any(f.code == "projection_short_of_target" for f in v.findings)


def test_projection_gate_ignores_non_charge_plan_short_of_target():
    # No grid-charge slot → the target is not a committed charge goal; don't hard-reject on it.
    plan = Plan(created_at=T0, slots=(_self(0), _self(1), _self(2), _self(3)),
                strategy="summer", target_soc=88.0, deadline=T0 + 4 * SLOT)
    proj = _proj(40.0, 45.0, 50.0, 55.0, 55.0)
    v = validate_plan(plan, projection=proj, **_ctx())
    assert not any(f.code == "projection_short_of_target" for f in v.findings)


def test_projection_gate_exempts_summer_charge_plan():
    plan = _charge_plan(88.0, deadline_slot=4)
    plan = Plan(created_at=plan.created_at, slots=plan.slots, strategy="summer",
                target_soc=plan.target_soc, deadline=plan.deadline)
    v = validate_plan(plan, projection=_proj(40.0, 45.0, 50.0, 55.0, 55.0), **_ctx())
    assert not any(f.code == "projection_short_of_target" for f in v.findings)


def test_projection_gate_checks_each_winter_peak_deadline():
    first = T0 + 2 * SLOT
    second = T0 + 5 * SLOT
    slots = (
        PlanSlot(T0, BatteryIntent.GRID_CHARGE_TO_TARGET, "first", target_soc=70, deadline=first),
        PlanSlot(T0 + SLOT, BatteryIntent.ALLOW_SELF_CONSUMPTION, "valley"),
        PlanSlot(first, BatteryIntent.ALLOW_SELF_CONSUMPTION, "peak"),
        PlanSlot(T0 + 3 * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, "second",
                 target_soc=88, deadline=second),
    )
    plan = Plan(created_at=T0, slots=slots, strategy="winter", target_soc=88, deadline=first)
    # First peak is reached, second is not; the old plan-level check incorrectly passed.
    projection = _proj(50, 68, 70, 65, 72, 72)
    v = validate_plan(plan, projection=projection, **_ctx())
    assert v.status == "warn"
    assert v.ok is True
    finding = next(f for f in v.findings if f.code == "projection_short_of_target")
    assert finding.severity == "warn"
    assert "88%" in finding.message and "72%" in finding.message


def test_projection_margin_is_exactly_five_points():
    plan = _charge_plan(88.0, deadline_slot=4)
    # Exactly five points short is allowed; one tenth below is rejected.
    assert not any(f.code == "projection_short_of_target" for f in
                   validate_plan(plan, projection=_proj(40, 60, 70, 83, 83), **_ctx()).findings)
    assert any(f.code == "projection_short_of_target" for f in
               validate_plan(plan, projection=_proj(40, 60, 70, 82.9, 82.9), **_ctx()).findings)


def test_projection_slot_starting_at_deadline_does_not_count():
    plan = _charge_plan(88.0, deadline_slot=4)
    # Slot 4 starts exactly at the deadline but ends afterward; its 90% result
    # must not mask the 70% result reached by the deadline.
    projection = _proj(40, 55, 62, 70, 90)
    finding = next((f for f in validate_plan(plan, projection=projection, **_ctx()).findings
                    if f.code == "projection_short_of_target"), None)
    assert finding is not None and "70%" in finding.message


# --- Grid fuse / netlimiet (#133) ----------------------------------------------------------------
def test_charge_plus_load_above_grid_limit_is_unsafe():
    # 4000 W charge + 2000 W house = 6000 W > 5750 W fuse → unsafe.
    v = validate_plan(
        _plan(_charge(0, power=4000.0)),
        grid_limit_w=5750.0, expected_load_w=2000.0, **_ctx(),
    )
    assert v.status == "unsafe" and not v.ok
    f = next(f for f in v.findings if f.code == "grid_limit_exceeded")
    assert "4000" in f.message and "2000" in f.message and "5750" in f.message


def test_charge_plus_load_under_grid_limit_is_valid():
    # 4000 W charge + 500 W house = 4500 W < 5750 W → ok.
    v = validate_plan(
        _plan(_charge(0, power=4000.0)),
        grid_limit_w=5750.0, expected_load_w=500.0, **_ctx(),
    )
    assert not any(f.code == "grid_limit_exceeded" for f in v.findings)
    assert v.ok is True


def test_grid_limit_zero_disables_check():
    v = validate_plan(
        _plan(_charge(0, power=4000.0)),
        grid_limit_w=0.0, expected_load_w=9000.0, **_ctx(),
    )
    assert not any(f.code == "grid_limit_exceeded" for f in v.findings)


def test_grid_limit_uses_per_slot_load_w_by():
    # Slot 0 load is over the fuse; slot 1 would be fine — one over-limit charge is enough.
    plan = _plan(_charge(0, power=4000.0), _charge(1, power=4000.0))
    load = {T0: 2500.0, T0 + SLOT: 100.0}
    v = validate_plan(plan, grid_limit_w=5750.0, load_w_by=load, **_ctx())
    assert any(f.code == "grid_limit_exceeded" for f in v.findings) and not v.ok


def test_grid_limit_uses_capability_when_slot_power_missing():
    slot = PlanSlot(T0, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge",
                    target_soc=80.0, floor_soc=10.0, power_w=None)
    v = validate_plan(
        _plan(slot), grid_limit_w=5000.0, expected_load_w=1500.0, **_ctx(),
    )
    # CAP.max_charge_w=4000 + 1500 = 5500 > 5000.
    assert any(f.code == "grid_limit_exceeded" for f in v.findings)


def test_non_charge_plan_ignores_grid_limit():
    v = validate_plan(
        _plan(_self(0)), grid_limit_w=100.0, expected_load_w=2000.0, **_ctx(),
    )
    assert not any(f.code == "grid_limit_exceeded" for f in v.findings)
    assert v.ok is True
