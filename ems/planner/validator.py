"""Hard plan validator (SPEC §8.11) — the gate every Plan passes before it may be applied.

Energy review #4/#6: "reject plans with impossible charging, missing target, stale inputs, invalid
SoC projection, excessive switch count, sub-dwell slot churn, or missing battery capability." The
result is advisory in dry-run (surfaced in the UI) and control-blocking when live: an `unsafe`
verdict means the controller must NOT apply the plan and stays on the battery's own AUTO
(self-consumption) — never worse than "no EMS" (CLAUDE.md "fail safe").

Pure + unit-tested: no I/O. The caller supplies the current SoC, the data-quality badge, optional
capability + projection, and the same dwell/switch limits the controller enforces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from ems.domain import BatteryIntent, CapabilityReport
from ems.planner.projection import ProjectedSlot
from ems.planner.schedule import Plan

# Severity order: unsafe (control-blocking) > warn (degraded, still usable) > (none).
_UNSAFE, _WARN = "unsafe", "warn"
_CHARGE_INTENTS = (BatteryIntent.GRID_CHARGE_TO_TARGET,)


@dataclass(frozen=True)
class Finding:
    severity: str  # "unsafe" | "warn"
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class PlanValidation:
    status: str  # "valid" | "warn" | "unsafe"
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """True when the plan may be applied (no control-blocking finding)."""
        return self.status != _UNSAFE

    def to_dict(self) -> dict:
        return {"status": self.status, "ok": self.ok,
                "findings": [f.to_dict() for f in self.findings]}


def validate_plan(
    plan: Plan,
    *,
    soc_pct: float | None,
    data_quality: str,
    min_reserve_soc: float,
    capability: CapabilityReport | None = None,
    projection: list[ProjectedSlot] | None = None,
    max_switches_per_day: int = 10,
    min_dwell: timedelta = timedelta(seconds=600),
    slot_horizon: int = 96,
    validate_projection: bool = True,
    projection_target_margin_pp: float = 5.0,
    grid_limit_w: float | None = None,
    expected_load_w: float | None = None,
    load_w_by: dict | None = None,
) -> PlanValidation:
    """Validate `plan` against the current conditions. Returns a PlanValidation; `unsafe` ⇒ the
    controller must hold AUTO. Each check appends at most one representative finding (not one per
    slot) so the result reads as a short, actionable list.

    `validate_projection` (default on — the SPEC §8.5 gate, BACKLOG B-22 / #162) adds the
    projected-target reachability check below. A shortfall is **warn** (plan stays applicable —
    honest-partial / best-effort charge); only reserve breaches and unsafe inputs block control.

    `grid_limit_w` (SPEC §8.11 / #133) is the main-fuse ceiling. When set (>0), any grid-charge
    slot whose charge power + expected house load exceeds it is `unsafe`. Prefer per-slot
    `load_w_by`; fall back to scalar `expected_load_w`. Zero/None disables the check."""
    findings: list[Finding] = []
    slots = plan.slots[:slot_horizon]

    # 1. Stale/missing critical inputs make any non-self-consumption action unsafe (matches the
    #    per-slot fail-safe, lifted to a plan-level gate). Unknown SoC (#134) is the same class of
    #    failure — never plan against a fabricated 0.0.
    if data_quality == "unsafe" or soc_pct is None:
        findings.append(Finding(_UNSAFE, "stale_inputs",
                                "Critical sensor data is stale or missing — holding self-use."))

    # 2. Target-SoC sanity on charge slots (the abstraction that lets us NOT default to full).
    charge = [s for s in slots if s.intent in _CHARGE_INTENTS]
    if any(s.target_soc is None for s in charge):
        findings.append(Finding(_WARN, "charge_target_unsized",
                                "A grid-charge slot has no target SoC yet — it won't execute until "
                                "sized (the driver refuses a target-less charge)."))
    for s in charge:
        t = s.target_soc
        if t is None:
            continue
        if not (0.0 <= t <= 100.0):
            findings.append(Finding(_UNSAFE, "target_out_of_range",
                                    f"Charge target {t:.0f}% is outside 0–100%."))
            break
        floor = s.floor_soc if s.floor_soc is not None else min_reserve_soc
        if t < floor:
            findings.append(Finding(_UNSAFE, "target_below_reserve",
                                    f"Charge target {t:.0f}% is below the reserve floor "
                                    f"{floor:.0f}% — impossible/contradictory."))
            break

    # 3. Power must not exceed what the battery can do (when capability is known). Check against the
    #    DIRECTION-appropriate limit — a charge slot vs max_charge_w, otherwise max_discharge_w.
    if capability is not None:
        for s in slots:
            if s.power_w is None:
                continue
            limit = (capability.max_charge_w if s.intent in _CHARGE_INTENTS
                     else capability.max_discharge_w)
            if s.power_w > limit + 1e-6:
                findings.append(Finding(_WARN, "power_exceeds_capability",
                                        f"A slot requests {s.power_w:.0f} W, above the battery's "
                                        f"{limit:.0f} W rated power."))
                break

    # 4. Excessive mode switches / sub-dwell churn — protect the battery from thrash.
    transitions = [(slots[i - 1], slots[i]) for i in range(1, len(slots))
                   if slots[i].intent is not slots[i - 1].intent]
    if len(transitions) > max_switches_per_day:
        findings.append(Finding(_WARN, "excessive_switches",
                                f"The plan switches mode {len(transitions)}× — above the "
                                f"{max_switches_per_day}/day budget."))
    if any((b.start - a.start) < min_dwell for a, b in transitions):
        findings.append(Finding(_WARN, "dwell_churn",
                                "The plan changes mode faster than the minimum dwell time."))

    # 5. Projected SoC must stay within [reserve, 100] when a projection is supplied. If the
    # battery is already below reserve, don't turn that starting condition into a false unsafe
    # finding; only block plans that make it worse. Skipped when SoC is unknown (#134).
    if projection and soc_pct is not None:
        floor = min(min_reserve_soc, soc_pct)
        if any(p.soc_pct < floor - 1e-6 for p in projection):
            findings.append(Finding(_UNSAFE, "projection_below_reserve",
                                    "The plan is projected to discharge below the reserve floor."))
        if any(p.soc_pct > 100.0 + 1e-6 for p in projection):
            findings.append(Finding(_WARN, "projection_overfill",
                                    "The plan is projected to overfill the battery."))

    # 6. Projected-target reachability (SPEC §8.5 / B-22, honest-partial #162): a plan that COMMITS
    #    to grid-charging toward a target SoC by a deadline, but whose own forward projection can't
    #    reach that target by a clear margin, is flagged. Scoped to winter grid-charge plans (a
    #    summer solar plan's target is weather-hoped, not committed). Data-quality-aware: only runs
    #    on `complete` inputs. Severity is **warn** (not unsafe): rejecting to AUTO with zero charge
    #    when the battery is already at the floor is a death spiral — prefer B-16 honest-partial
    #    (lower target, keep charging). Reserve breaches stay unsafe via check #5; stale/missing
    #    inputs stay unsafe via check #1.
    if (validate_projection and projection and data_quality == "complete"
            and plan.strategy == "winter"
            and plan.target_soc is not None and plan.deadline is not None
            and any(s.intent in _CHARGE_INTENTS for s in slots)):
        # Check each committed charge window independently.  Winter plans can span
        # multiple peaks while the plan-level deadline refers to the first one.
        commitments = {(s.deadline or plan.deadline,
                        s.target_soc if s.target_soc is not None else plan.target_soc)
                       for s in slots if s.intent in _CHARGE_INTENTS}
        for deadline, target in sorted(commitments, key=lambda x: x[0]):
            # ProjectedSlot.soc_pct is the *end* SoC of its slot.  A slot that
            # starts at the deadline has not completed by the deadline and must
            # not count toward reachability.
            reached_values = [p.soc_pct for p in projection
                              if p.start + timedelta(minutes=15) <= deadline]
            if reached_values:
                # Use the value at the latest projected instant, not the maximum
                # transient value (which can hide a drop before the deadline).
                reached = reached_values[-1]
                if reached >= target - projection_target_margin_pp:
                    continue
                when = deadline.strftime("%H:%M")
                findings.append(Finding(
                    _WARN, "projection_short_of_target",
                    f"Plan targets {target:.0f}% by {when} but projects only "
                    f"{reached:.0f}% — charging best-effort toward what's reachable "
                    f"(honest partial; not holding AUTO)."))
                break

    # 7. Grid fuse / netlimiet (#133): charge power + expected house load must not exceed the
    #    configured main-fuse ceiling. Conservative (ignores solar credit) so a cloudy slot
    #    can't trip the hoofdzekering. Mode-switch only — never a live power-tracking loop.
    #    Disabled when grid_limit_w is None/≤0. Skips a slot when charge power or load is unknown
    #    (other gates cover missing inputs; we don't invent load).
    if grid_limit_w is not None and grid_limit_w > 0:
        for s in charge:
            charge_w = s.power_w
            if charge_w is None and capability is not None:
                charge_w = capability.max_charge_w
            if charge_w is None:
                continue
            if load_w_by is not None and s.start in load_w_by:
                load_w = float(load_w_by[s.start])
            elif expected_load_w is not None:
                load_w = float(expected_load_w)
            else:
                continue
            demand_w = charge_w + max(0.0, load_w)
            if demand_w > grid_limit_w + 1e-6:
                findings.append(Finding(
                    _UNSAFE, "grid_limit_exceeded",
                    f"Grid-charge {charge_w:.0f} W + house load {load_w:.0f} W = "
                    f"{demand_w:.0f} W exceeds the grid fuse limit "
                    f"{grid_limit_w:.0f} W — holding self-use."))
                break

    status = (_UNSAFE if any(f.severity == _UNSAFE for f in findings)
              else _WARN if findings else "valid")
    return PlanValidation(status=status, findings=tuple(findings))
