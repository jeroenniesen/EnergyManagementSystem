"""Charge-completion + missed-window recovery (SPEC §8.12, BACKLOG B-16).

Pure planner tests with canned prices/SoC. Plans are FUTURE-only (slots from `now`), exactly like
a live horizon-filtered plan, so the projection gate behaves as it does in production. Covers the
on-pace / behind / missed classification, the cheapest-remaining catch-up (numeric identity against
the shared per-slot sizing), the honest partial when the hours run out, and that a recovered plan
passes the §8.11 validator (incl. the B-22 projection gate)."""
import math
from datetime import UTC, datetime, timedelta

from ems.domain import BatteryIntent, CapabilityReport
from ems.planner.charge_need import stored_kwh_per_slot
from ems.planner.projection import BatteryModel, project_energy
from ems.planner.recovery import (
    BEHIND,
    COMPLETE,
    MISSED,
    NOT_APPLICABLE,
    ON_PACE,
    build_catch_up_plan,
    check_charge_completion,
    recover_if_needed,
)
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.validator import validate_plan
from ems.sources.prices import PriceSlot

T0 = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)  # a winter night
DEADLINE = T0 + 30 * SLOT  # 07:30 — the morning peak

USABLE = 10.0
RESERVE = 10.0
MAX_CHARGE_W = 4000.0
RTE = 0.90
SLOT_KWH = stored_kwh_per_slot(MAX_CHARGE_W, RTE)  # ≈0.949 kWh DC per full-power slot


def _price(i: int, eur: float) -> PriceSlot:
    return PriceSlot(start=T0 + i * SLOT, eur_per_kwh=eur)


def _slot(i: int, intent: BatteryIntent, **kw) -> PlanSlot:
    return PlanSlot(T0 + i * SLOT, intent, "x", **kw)


def _committed_plan(*, charge_idx=(), start_idx=0, end_idx=32, peak=(28, 29),
                    target_soc=80.0, deadline=DEADLINE) -> Plan:
    """A committed winter plan over slots [start_idx, end_idx): grid-charge at `charge_idx`,
    discharge the morning peak, else self-consumption. A future-only horizon like the live plan."""
    slots = []
    for i in range(start_idx, end_idx):
        if i in charge_idx:
            slots.append(_slot(i, BatteryIntent.GRID_CHARGE_TO_TARGET, target_soc=target_soc,
                               power_w=MAX_CHARGE_W, floor_soc=RESERVE, deadline=deadline))
        elif i in peak:
            slots.append(_slot(i, BatteryIntent.DISCHARGE_FOR_LOAD))
        else:
            slots.append(_slot(i, BatteryIntent.ALLOW_SELF_CONSUMPTION))
    return Plan(created_at=T0, slots=tuple(slots), strategy="winter",
                target_soc=target_soc, deadline=deadline)


# --------------------------------------------------------------------------------------------------
# check_charge_completion
# --------------------------------------------------------------------------------------------------
def test_not_applicable_without_committed_target():
    plan = Plan(created_at=T0, slots=(_slot(0, BatteryIntent.ALLOW_SELF_CONSUMPTION),),
                strategy="winter")  # no target/deadline
    s = check_charge_completion(plan, T0, 50.0)
    assert s.status == NOT_APPLICABLE and s.needs_recovery is False


def test_complete_when_at_or_above_target():
    plan = _committed_plan(charge_idx=range(8, 16))
    s = check_charge_completion(plan, T0 + 20 * SLOT, 82.0)
    assert s.status == COMPLETE and s.gap_pp == 0.0 and s.needs_recovery is False


def test_on_pace_within_margin():
    plan = _committed_plan(charge_idx=range(8, 16))  # target 80
    s = check_charge_completion(plan, T0 + 20 * SLOT, 77.0, margin_pp=5.0)  # 3 pp short
    assert s.status == ON_PACE and s.needs_recovery is False


def test_behind_within_ramp_passes():
    # Mid-window, still short but future charge slots remain and we're on the tolerated ramp.
    plan = _committed_plan(charge_idx=range(8, 24))  # charges 02:00–06:00
    s = check_charge_completion(plan, T0 + 12 * SLOT, 40.0)  # 03:00, short but early on the ramp
    assert s.status == BEHIND and s.needs_recovery is False


def test_missed_when_deadline_passed_and_short():
    plan = _committed_plan(charge_idx=range(8, 16), end_idx=34)
    s = check_charge_completion(plan, DEADLINE + SLOT, 45.0)  # past 07:30, 35 pp short
    assert s.status == MISSED and s.needs_recovery is True
    assert "deadline" in s.reason


def test_missed_when_nothing_scheduled_before_deadline():
    # Committed target, deadline ahead, but the arbitrage pool was priced out → NO charge slots.
    plan = _committed_plan(charge_idx=())
    s = check_charge_completion(plan, T0 + 4 * SLOT, 30.0)  # 01:00, 50 pp short, nothing planned
    assert s.status == MISSED and s.needs_recovery is True
    assert "nothing scheduled" in s.reason


def test_missed_off_ramp_late_in_window():
    # Charge slots exist but we're near the deadline and still far short → off the ramp.
    plan = _committed_plan(charge_idx=range(8, 16))
    s = check_charge_completion(plan, DEADLINE - SLOT, 45.0)  # 07:15, 35 pp short
    assert s.status == MISSED and s.needs_recovery is True


# --------------------------------------------------------------------------------------------------
# build_catch_up_plan
# --------------------------------------------------------------------------------------------------
def _build(soc, plan, prices, now=T0, **kw):
    base = dict(soc_pct=soc, prices=prices, usable_kwh=USABLE, reserve_soc_pct=RESERVE,
                max_charge_w=MAX_CHARGE_W, round_trip_efficiency=RTE)
    base.update(kw)
    return build_catch_up_plan(plan, now, **base)


def test_catch_up_sizes_cheapest_remaining_slots_numeric_identity():
    # 50 pp short of 80% on a 10 kWh pack = 5.0 kWh DC. Each slot stores SLOT_KWH → ceil(5/SLOT).
    plan = _committed_plan(charge_idx=())  # priced out, no charge slots
    # slots 8..13 clearly cheapest; the peak and the rest dear.
    prices = [_price(i, 0.10 if 8 <= i <= 13 else (0.40 if i in (28, 29) else 0.30))
              for i in range(32)]
    r = _build(30.0, plan, prices)  # now=00:00, target 80% → 5.0 kWh short
    expected_slots = math.ceil((80.0 - 30.0) / 100.0 * USABLE / SLOT_KWH)
    assert expected_slots == 6
    assert r.feasible is True
    assert r.slots_used == expected_slots
    assert r.target_soc == 80.0 and r.kwh_short == 0.0
    charge = [s for s in r.plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert len(charge) == expected_slots
    assert {s.start for s in charge} == {T0 + i * SLOT for i in range(8, 14)}  # the cheapest six
    assert all(s.power_w == MAX_CHARGE_W and s.target_soc == 80.0 for s in charge)
    assert all(s.deadline == DEADLINE for s in charge)
    assert "catch-up" in r.reason and "80%" in r.reason


def test_catch_up_never_overwrites_a_discharge_peak():
    plan = _committed_plan(charge_idx=())
    # Make the peak slots (28,29) the very cheapest — they must STILL not become charge slots.
    prices = [_price(i, 0.01 if i in (28, 29) else 0.30) for i in range(32)]
    r = _build(30.0, plan, prices)
    charge_starts = {s.start for s in r.plan.slots
                     if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET}
    assert (T0 + 28 * SLOT) not in charge_starts and (T0 + 29 * SLOT) not in charge_starts


def test_original_charge_slots_are_superseded_not_double_counted():
    # A plan that DID have (now-superseded) charge slots must charge in EXACTLY the chosen set.
    plan = _committed_plan(charge_idx=range(4, 20))  # lots of stale charge slots
    prices = [_price(i, 0.10 if 8 <= i <= 13 else 0.30) for i in range(32)]
    r = _build(30.0, plan, prices)
    charge = [s for s in r.plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert {s.start for s in charge} == {T0 + i * SLOT for i in range(8, 14)}


def test_impossible_catch_up_is_honest_partial():
    # Deadline very close: from now only two slots remain, far short of the 5 kWh needed.
    near = T0 + 12 * SLOT  # 03:00
    plan = _committed_plan(charge_idx=(), start_idx=10, peak=(), deadline=near, target_soc=80.0)
    prices = [_price(i, 0.10) for i in range(32)]
    r = _build(30.0, plan, prices, now=T0 + 10 * SLOT)  # 02:30 → only slots 10,11 before 03:00
    assert r.slots_used == 2
    assert r.feasible is False
    assert r.target_soc < 80.0 and r.kwh_short > 0.0
    assert "partial" in r.reason and "not 80%" in r.reason
    assert r.target_soc >= RESERVE  # never proposes a target below the reserve floor
    assert "short" in r.note.lower()


# --------------------------------------------------------------------------------------------------
# recovered plan passes the §8.11 validator (incl. B-22 projection gate)
# --------------------------------------------------------------------------------------------------
CAP = CapabilityReport(services=("charge", "discharge"), energy_mode_options=(),
                       has_standby=True, has_grid_charge_switch=True, p1_paired=True,
                       max_charge_w=MAX_CHARGE_W, max_discharge_w=MAX_CHARGE_W)


def _project(plan, soc):
    model = BatteryModel(usable_kwh=USABLE, max_charge_w=MAX_CHARGE_W, max_discharge_w=MAX_CHARGE_W,
                         round_trip_efficiency=RTE, reserve_soc_pct=RESERVE)
    load_by = {s.start: 200.0 for s in plan.slots}  # light overnight load
    return project_energy(plan.slots, start_soc_pct=soc, solar_w_by={}, load_w_by=load_by,
                          model=model, charge_target_soc_pct=None)


def _validate(plan, soc):
    return validate_plan(plan, soc_pct=soc, data_quality="complete", min_reserve_soc=RESERVE,
                         capability=CAP, projection=_project(plan, soc), validate_projection=True,
                         min_dwell=timedelta(seconds=0))


def test_recovered_plan_passes_validator_including_projection_gate():
    plan = _committed_plan(charge_idx=())
    prices = [_price(i, 0.10 if 8 <= i <= 13 else 0.30) for i in range(32)]
    r = _build(30.0, plan, prices)
    v = _validate(r.plan, 30.0)
    assert v.ok, [f.to_dict() for f in v.findings]


def test_partial_recovery_target_is_reachable_by_its_own_projection():
    # The lowered partial target must be self-consistent: check #6 must not reject it.
    near = T0 + 12 * SLOT
    plan = _committed_plan(charge_idx=(), start_idx=10, peak=(), deadline=near, target_soc=80.0)
    prices = [_price(i, 0.10) for i in range(32)]
    r = _build(30.0, plan, prices, now=T0 + 10 * SLOT)
    v = _validate(r.plan, 30.0)
    assert v.ok, [f.to_dict() for f in v.findings]


# --------------------------------------------------------------------------------------------------
# recover_if_needed (the pure control-loop entry point)
# --------------------------------------------------------------------------------------------------
def _recover(soc, plan, prices, now, **kw):
    base = dict(soc_pct=soc, prices=prices, usable_kwh=USABLE, reserve_soc_pct=RESERVE,
                max_charge_w=MAX_CHARGE_W, round_trip_efficiency=RTE)
    base.update(kw)
    return recover_if_needed(plan, now, **base)


def test_recover_if_needed_disabled_is_todays_behaviour():
    plan = _committed_plan(charge_idx=(), end_idx=34)
    prices = [_price(i, 0.10) for i in range(34)]
    out_plan, status, catch = _recover(30.0, plan, prices, DEADLINE + SLOT, enabled=False)
    assert out_plan is plan and catch is None
    assert status.status == MISSED  # still diagnosed, just not acted on


def test_recover_if_needed_on_pace_returns_plan_unchanged():
    plan = _committed_plan(charge_idx=range(8, 16))
    prices = [_price(i, 0.10) for i in range(32)]
    out_plan, status, catch = _recover(78.0, plan, prices, T0 + 20 * SLOT)  # within margin
    assert out_plan is plan and catch is None and status.status == ON_PACE


def test_recover_if_needed_missed_returns_catch_up_plan():
    plan = _committed_plan(charge_idx=())  # priced out: no charge slots
    prices = [_price(i, 0.10 if 8 <= i <= 20 else 0.30) for i in range(32)]
    out_plan, status, catch = _recover(30.0, plan, prices, T0 + 4 * SLOT)
    assert status.status == MISSED and catch is not None
    assert out_plan is catch.plan
    assert any(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in out_plan.slots)


def test_recover_if_needed_skips_summer_solar_hoped_target():
    # A summer solar-first target is weather-hoped, NOT a grid-charge commitment — recovery must not
    # grid-charge to "catch it up" (the documented over-buy). Diagnosed missed, but never acted on.
    plan = _committed_plan(charge_idx=())
    plan = Plan(created_at=T0, slots=plan.slots, strategy="summer",
                target_soc=80.0, deadline=DEADLINE)
    prices = [_price(i, 0.10) for i in range(32)]
    out_plan, status, catch = _recover(30.0, plan, prices, T0 + 4 * SLOT)
    assert out_plan is plan and catch is None
    assert status.status == MISSED  # still diagnosed, just not a grid-charge catch-up
