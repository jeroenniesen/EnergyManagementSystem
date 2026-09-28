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

from dataclasses import dataclass, field, replace
from datetime import timedelta

from ems.domain import BatteryIntent, CapabilityReport
from ems.planner.projection import ProjectedSlot
from ems.planner.schedule import Plan, PlanSlot

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


def effective_power_limit_w(
    *,
    capability_w: float | None,
    settings_w: float | None,
) -> float | None:
    """Capability-driven charge/discharge ceiling: min(settings, capability) when both known.

    Prefer the live CapabilityReport over a hardcoded wattage so Gen-2 (or any device whose
    real rating exceeds the SolidFlex OpenData 2400 W/tower default) is not silently capped
    below what the probe advertised (#164)."""
    if capability_w is None and settings_w is None:
        return None
    if capability_w is None:
        return float(settings_w) if settings_w is not None else None
    if settings_w is None:
        return float(capability_w)
    return float(min(capability_w, settings_w))


def clamp_plan_power(
    plan: Plan,
    *,
    capability: CapabilityReport | None,
    settings_max_charge_w: float | None = None,
    settings_max_discharge_w: float | None = None,
) -> tuple[Plan, tuple[Finding, ...]]:
    """Clamp each slot's `power_w` to min(settings, capability) **before** §8.11 validate (#164).

    Stops a healthy catch-up/recovery plan sized at settings (e.g. 4800 W for 2×2400) from being
    rejected or warned solely because CapabilityReport under-reported the cluster total. Returns
    the (possibly replaced) plan plus warn findings when settings and capability diverge or a
    slot was clamped. Pure — no I/O."""
    findings: list[Finding] = []
    if capability is None:
        return plan, ()

    charge_limit = effective_power_limit_w(
        capability_w=capability.max_charge_w, settings_w=settings_max_charge_w,
    )
    discharge_limit = effective_power_limit_w(
        capability_w=capability.max_discharge_w, settings_w=settings_max_discharge_w,
    )

    # Debugability: surface settings vs capability divergence even when no slot needs a clamp.
    if (settings_max_charge_w is not None
            and abs(float(settings_max_charge_w) - capability.max_charge_w) > 1e-6):
        findings.append(Finding(
            _WARN, "settings_capability_power_mismatch",
            f"Settings max_charge_w={settings_max_charge_w:.0f} W diverges from capability "
            f"{capability.max_charge_w:.0f} W — clamping plan power to "
            f"{charge_limit:.0f} W (the lower figure).",
        ))
    elif (settings_max_discharge_w is not None
          and abs(float(settings_max_discharge_w) - capability.max_discharge_w) > 1e-6):
        findings.append(Finding(
            _WARN, "settings_capability_power_mismatch",
            f"Settings max_discharge_w={settings_max_discharge_w:.0f} W diverges from capability "
            f"{capability.max_discharge_w:.0f} W — clamping plan power to "
            f"{discharge_limit:.0f} W (the lower figure).",
        ))

    new_slots: list[PlanSlot] = []
    changed = False
    for s in plan.slots:
        if s.power_w is None:
            new_slots.append(s)
            continue
        limit = charge_limit if s.intent in _CHARGE_INTENTS else discharge_limit
        if limit is None or s.power_w <= limit + 1e-6:
            new_slots.append(s)
            continue
        new_slots.append(replace(s, power_w=float(limit)))
        changed = True
        if not any(f.code == "power_clamped_to_capability" for f in findings):
            findings.append(Finding(
                _WARN, "power_clamped_to_capability",
                f"A slot requested {s.power_w:.0f} W; clamped to {limit:.0f} W "
                f"(min of settings and capability) before validate.",
            ))

    if not changed:
        return plan, tuple(findings)
    return replace(plan, slots=tuple(new_slots)), tuple(findings)


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
    settings_max_charge_w: float | None = None,
    settings_max_discharge_w: float | None = None,
) -> PlanValidation:
    """Validate `plan` against the current conditions. Returns a PlanValidation; `unsafe` ⇒ the
    controller must hold AUTO. Each check appends at most one representative finding (not one per
    slot) so the result reads as a short, actionable list.

    `validate_projection` (default on — the SPEC §8.5 "later step", BACKLOG B-22) adds the
    projected-target reachability gate below. It is a pure safety net: a rejection just falls back
    to AUTO, which is never worse than "no EMS", so it defaults on.

    `grid_limit_w` (SPEC §8.11 / #133) is the main-fuse ceiling. When set (>0), any grid-charge
    slot whose charge power + expected house load exceeds it is `unsafe`. Prefer per-slot
    `load_w_by`; fall back to scalar `expected_load_w`. Zero/None disables the check.

    Optional `settings_max_*_w` enrich the power-exceeds finding when settings and capability
    diverge (#164) — callers that already ran `clamp_plan_power` normally won't hit that check."""
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
    #    Prefer min(settings, capability) when settings are supplied so a divergent pair is named
    #    in the finding (#164) rather than a bare capability number.
    if capability is not None:
        for s in slots:
            if s.power_w is None:
                continue
            cap_limit = (capability.max_charge_w if s.intent in _CHARGE_INTENTS
                         else capability.max_discharge_w)
            settings_w = (settings_max_charge_w if s.intent in _CHARGE_INTENTS
                          else settings_max_discharge_w)
            limit = effective_power_limit_w(capability_w=cap_limit, settings_w=settings_w)
            if limit is None:
                continue
            if s.power_w > limit + 1e-6:
                msg = (f"A slot requests {s.power_w:.0f} W, above the battery's "
                       f"{limit:.0f} W rated power.")
                if (settings_w is not None
                        and abs(float(settings_w) - float(cap_limit)) > 1e-6):
                    msg += (f" Settings advertise {float(settings_w):.0f} W vs capability "
                            f"{cap_limit:.0f} W.")
                findings.append(Finding(_WARN, "power_exceeds_capability", msg))
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

    # 6. Projected-target reachability (SPEC §8.5 "later step", BACKLOG B-22): a plan that COMMITS
    #    to grid-charging toward a target SoC by a deadline, but whose own forward projection can't
    #    reach that target by a clear margin, is rejected → fail safe to AUTO. Scoped to grid-charge
    #    plans (a summer solar plan's target is weather-hoped, not committed — that's the top-up
    #    logic's job, not a hard reject). Data-quality-aware: only runs on `complete` inputs, so a
    #    missing/stale forecast never triggers it — that path is the data fail-safe's, not ours.
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
                    _UNSAFE, "projection_short_of_target",
                    f"Plan targets {target:.0f}% by {when} but projects only "
                    f"{reached:.0f}% — the charge windows can't reach it in time."))
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
                charge_w = effective_power_limit_w(
                    capability_w=capability.max_charge_w,
                    settings_w=settings_max_charge_w,
                )
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
