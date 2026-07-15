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
    assert v.status == "unsafe" and v.ok is False
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


def test_power_above_capability_warns():
    v = validate_plan(_plan(_charge(0, power=9000.0)), **_ctx())
    assert any(f.code == "power_exceeds_capability" for f in v.findings) and v.ok is True


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
    slots = [_self(0), _charge(1), _self(2)]  # charge held one 15-min slot, exited < 30 min later
    v = validate_plan(_plan(*slots), **_ctx(), min_dwell=timedelta(minutes=30))
    assert any(f.code == "dwell_churn" for f in v.findings)


def test_long_holds_are_not_dwell_churn():
    # Each mode is held for a full hour; with a 30-min dwell floor this is NOT churn. The broken
    # check compared adjacent slots (always 15 min apart) and wrongly flagged any transition.
    slots = [_self(i) for i in range(4)] + [_charge(i) for i in range(4, 8)] \
        + [_self(i) for i in range(8, 12)]
    v = validate_plan(_plan(*slots), **_ctx(), min_dwell=timedelta(minutes=30))
    assert not any(f.code == "dwell_churn" for f in v.findings)


def test_brief_mode_blip_between_long_holds_is_churn():
    # A single-slot charge wedged between two long self runs is held only 15 min → churn at 30-min.
    slots = [_self(i) for i in range(4)] + [_charge(4)] + [_self(i) for i in range(5, 9)]
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


def test_projection_short_of_target_is_unsafe_with_the_numbers():
    # Plan commits to 88% by slot 4, but the projection tops out at 71% → clear (>5pp) shortfall.
    plan = _charge_plan(88.0, deadline_slot=4)
    proj = _proj(40.0, 55.0, 65.0, 71.0, 71.0)
    v = validate_plan(plan, projection=proj, **_ctx())
    assert v.status == "unsafe"
    f = next(f for f in v.findings if f.code == "projection_short_of_target")
    assert "88%" in f.message and "71%" in f.message


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
    assert v.status == "unsafe"
    finding = next(f for f in v.findings if f.code == "projection_short_of_target")
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
