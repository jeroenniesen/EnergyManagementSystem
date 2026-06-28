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
    soc_pct: float,
    data_quality: str,
    min_reserve_soc: float,
    capability: CapabilityReport | None = None,
    projection: list[ProjectedSlot] | None = None,
    max_switches_per_day: int = 10,
    min_dwell: timedelta = timedelta(seconds=600),
    slot_horizon: int = 96,
) -> PlanValidation:
    """Validate `plan` against the current conditions. Returns a PlanValidation; `unsafe` ⇒ the
    controller must hold AUTO. Each check appends at most one representative finding (not one per
    slot) so the result reads as a short, actionable list."""
    findings: list[Finding] = []
    slots = plan.slots[:slot_horizon]

    # 1. Stale/missing critical inputs make any non-self-consumption action unsafe (matches the
    #    per-slot fail-safe, lifted to a plan-level gate).
    if data_quality == "unsafe":
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

    # 5. Projected SoC must stay within [reserve, 100] when a projection is supplied.
    if projection:
        if any(p.soc_pct < min_reserve_soc - 1e-6 for p in projection):
            findings.append(Finding(_UNSAFE, "projection_below_reserve",
                                    "The plan is projected to discharge below the reserve floor."))
        if any(p.soc_pct > 100.0 + 1e-6 for p in projection):
            findings.append(Finding(_WARN, "projection_overfill",
                                    "The plan is projected to overfill the battery."))

    status = (_UNSAFE if any(f.severity == _UNSAFE for f in findings)
              else _WARN if findings else "valid")
    return PlanValidation(status=status, findings=tuple(findings))
