"""Structured decision-reason schema for optimization explainability (B-74 / #84).

Every plan decision carries the same factual object so web, iOS, logs and diagnostics can render
one honest story. Slice 1 defines the schema + exposes it on `/api/battery-plan`. Slice 2 renders
that same object in the web UI and includes it in logs / diagnostics export (no new schema).
Slice 3 (iOS) is out of this module.

Pure builders — no I/O, no device writes. The reason is assembled from the **same** recovered plan
the control path acts on (`plan_with_recovery` / `current_plan`) plus the factual gate outcomes of
the decide path (validator finding, failsafe, dwell, switch-cap, unconfirmed).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ems.domain import BatteryIntent
from ems.planner.schedule import Plan, PlanSlot
from ems.savings import estimate_daily_savings_eur

_CHARGE = BatteryIntent.GRID_CHARGE_TO_TARGET
_DISCHARGE = BatteryIntent.DISCHARGE_FOR_LOAD
_HOLD = BatteryIntent.HOLD_RESERVE
_SELF = BatteryIntent.ALLOW_SELF_CONSUMPTION

# Homeowner-facing action vocabulary aligned with /api/battery-plan current_action.
_ACTION_PAUSED = "paused"
_ACTION_PROCEED = "proceed"

# Stable top-level keys of DecisionReason.to_dict() — contract for web / logs / diagnostics (#84).
REASON_DICT_KEYS = frozenset({
    "chosen_window", "rejected_alternative", "expected_benefit", "risk",
    "safety_constraint", "gates", "summary",
})
GATE_DICT_KEYS = frozenset({
    "validator_code", "failsafe", "dwell", "cap_reached", "unconfirmed",
})


@dataclass(frozen=True)
class ChosenWindow:
    """The window the planner selected (typically the cheap charge block, else discharge/hold)."""

    start: datetime | None
    end: datetime | None
    intent: str | None
    label: str | None
    eur_per_kwh_min: float | None = None
    eur_per_kwh_max: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat() if self.start is not None else None,
            "end": self.end.isoformat() if self.end is not None else None,
            "intent": self.intent,
            "label": self.label,
            "eur_per_kwh_min": self.eur_per_kwh_min,
            "eur_per_kwh_max": self.eur_per_kwh_max,
        }


@dataclass(frozen=True)
class RejectedAlternative:
    """A concrete alternative the planner considered and rejected (or the no-trade path)."""

    intent: str | None
    reason: str
    window_start: datetime | None = None
    window_end: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "reason": self.reason,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
        }


@dataclass(frozen=True)
class ExpectedBenefit:
    """Expected net benefit of following the plan (€, conservative estimate)."""

    eur: float | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {"eur": self.eur, "summary": self.summary}


@dataclass(frozen=True)
class Risk:
    """Risk / wear margin applied when sizing the arbitrage."""

    margin_eur_per_kwh: float | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "margin_eur_per_kwh": self.margin_eur_per_kwh,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class SafetyConstraint:
    """Why control is held or free to act — code matches the §8.11 finding when unsafe."""

    code: str | None
    message: str | None
    action: str  # "paused" | "proceed"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "action": self.action}


@dataclass(frozen=True)
class GateOutcomes:
    """Factual outcomes of the control gates for this decision cycle."""

    validator_code: str | None
    failsafe: bool
    dwell: bool
    cap_reached: bool
    unconfirmed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator_code": self.validator_code,
            "failsafe": self.failsafe,
            "dwell": self.dwell,
            "cap_reached": self.cap_reached,
            "unconfirmed": self.unconfirmed,
        }


@dataclass(frozen=True)
class DecisionReason:
    """Cross-surface structured reason for one optimization / control decision (B-74)."""

    chosen_window: ChosenWindow | None
    rejected_alternative: RejectedAlternative | None
    expected_benefit: ExpectedBenefit | None
    risk: Risk | None
    safety_constraint: SafetyConstraint
    gates: GateOutcomes
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "chosen_window": self.chosen_window.to_dict() if self.chosen_window else None,
            "rejected_alternative": (
                self.rejected_alternative.to_dict() if self.rejected_alternative else None
            ),
            "expected_benefit": (
                self.expected_benefit.to_dict() if self.expected_benefit else None
            ),
            "risk": self.risk.to_dict() if self.risk else None,
            "safety_constraint": self.safety_constraint.to_dict(),
            "gates": self.gates.to_dict(),
            "summary": self.summary,
        }


def format_reason_log_line(reason: dict[str, Any] | DecisionReason) -> str:
    """One compact log line from the structured reason — same facts web/diagnostics render."""
    d = reason.to_dict() if isinstance(reason, DecisionReason) else reason
    safety = d.get("safety_constraint") or {}
    benefit = d.get("expected_benefit") or {}
    chosen = d.get("chosen_window") or {}
    gates = d.get("gates") or {}
    summary = str(d.get("summary") or "-").replace("\n", " ").strip()
    bits = [
        f"action={safety.get('action') or '-'}",
        f"summary={summary!r}",
    ]
    if chosen.get("intent"):
        bits.append(f"window={chosen.get('intent')}")
    if chosen.get("label"):
        bits.append(f"label={chosen.get('label')!r}")
    eur = benefit.get("eur")
    if eur is not None:
        bits.append(f"benefit_eur={eur}")
    if safety.get("code"):
        bits.append(f"safety={safety.get('code')}")
    if gates.get("validator_code"):
        bits.append(f"validator={gates.get('validator_code')}")
    for g in ("failsafe", "dwell", "cap_reached", "unconfirmed"):
        if gates.get(g):
            bits.append(f"{g}=1")
    return "decision.reason " + " ".join(bits)


def _slot_block(slots: tuple[PlanSlot, ...], intent: BatteryIntent) -> tuple[PlanSlot, ...] | None:
    """First contiguous run of `intent` slots (the chosen window for that intent)."""
    block: list[PlanSlot] = []
    for s in slots:
        if s.intent is intent:
            block.append(s)
        elif block:
            break
    # Prefer the earliest contiguous run; if intent appears later after a gap, restart.
    if block:
        return tuple(block)
    return None


def _window_from_block(
    block: tuple[PlanSlot, ...],
    price_by: dict[datetime, float],
    *,
    label: str,
) -> ChosenWindow:
    prices = [price_by[s.start] for s in block if s.start in price_by]
    return ChosenWindow(
        start=block[0].start,
        end=block[-1].slot_end,
        intent=str(block[0].intent),
        label=label,
        eur_per_kwh_min=round(min(prices), 4) if prices else None,
        eur_per_kwh_max=round(max(prices), 4) if prices else None,
    )


def _chosen_window(plan: Plan, price_by: dict[datetime, float]) -> ChosenWindow | None:
    """Prefer the grid-charge window; else discharge; else hold; else current self-consume span."""
    if not plan.slots:
        return None
    for intent, label in (
        (_CHARGE, "cheap charge window"),
        # DISCHARGE_FOR_LOAD is vendor self-consumption serving the house — not forced dump.
        (_DISCHARGE, "expensive self-consumption window"),
        (_HOLD, "hold-reserve window"),
    ):
        block = _slot_block(plan.slots, intent)
        if block:
            return _window_from_block(block, price_by, label=label)
    # No-trade / self-consume-only plan: report the full horizon as the chosen (passive) window.
    return _window_from_block(plan.slots, price_by, label="self-consumption window")


def _rejected_alternative(plan: Plan) -> RejectedAlternative | None:
    """Derive the rejected alternative from plan slot reasons (deterministic, no second planner)."""
    if not plan.slots:
        return None
    reasons = [s.reason for s in plan.slots if s.reason]
    # No-trade day: the plan rejected arbitrage.
    for r in reasons:
        low = r.lower()
        if "no-trade" in low or "spread below" in low:
            return RejectedAlternative(
                intent=str(_CHARGE),
                reason=r.split("(")[0].strip().rstrip(":") or r,
            )
    charge = [s for s in plan.slots if s.intent is _CHARGE]
    if charge:
        # Actively arbitraging → rejected the do-nothing self-consumption alternative.
        return RejectedAlternative(
            intent=str(_SELF),
            reason="self-consumption only — rejected in favour of the selected charge window",
            window_start=charge[0].start,
            window_end=charge[-1].slot_end,
        )
    discharge = [s for s in plan.slots if s.intent is _DISCHARGE]
    if discharge:
        return RejectedAlternative(
            intent=str(_SELF),
            reason=(
                "holding through the peak — rejected in favour of self-consumption "
                "to serve house load"
            ),
            window_start=discharge[0].start,
            window_end=discharge[-1].slot_end,
        )
    return RejectedAlternative(
        intent=str(_CHARGE),
        reason="grid charge — not selected for this horizon",
    )


def _top_unsafe_code(validation: Any | None) -> tuple[str | None, str | None]:
    """Return (code, message) of the first control-blocking finding, if any."""
    if validation is None:
        return None, None
    findings = getattr(validation, "findings", None) or ()
    for f in findings:
        if getattr(f, "severity", None) == "unsafe":
            return getattr(f, "code", None), getattr(f, "message", None)
    # Fallback: first finding when status is unsafe even without severity tag.
    status = getattr(validation, "status", None)
    if status == "unsafe" and findings:
        f0 = findings[0]
        return getattr(f0, "code", None), getattr(f0, "message", None)
    return None, None


def _homeowner_summary(raw: str | None, chosen: ChosenWindow | None) -> str:
    """Turn planner-internal slot reasons into homeowner Summary copy.

    Winter peak slots used to say ``discharge: €… > break-even €…`` — that reads as a forced
    dump next to the ``expensive self-consumption window`` label. Rewrite the bare ``discharge:``
    prefix; leave other reasons (manual override, car cover, fail-safe) untouched.
    """
    if raw:
        text = raw.strip()
        low = text.lower()
        if low.startswith("discharge:"):
            rest = text.split(":", 1)[1].strip()
            if rest:
                return f"Self-consumption while {rest}"
            return (
                chosen.label
                if chosen and chosen.label
                else "Self-consumption to serve house load"
            )
        return text
    if chosen and chosen.label:
        return chosen.label
    return "Following the current plan."


def empty_decision_reason(
    *,
    summary: str = "No plan is available yet.",
    validator_code: str | None = None,
    failsafe: bool = False,
    dwell: bool = False,
    cap_reached: bool = False,
    unconfirmed: bool = False,
    safety_message: str | None = None,
) -> DecisionReason:
    """Paused / empty contract — same shape so clients never see a missing `reason` key."""
    code = validator_code
    return DecisionReason(
        chosen_window=None,
        rejected_alternative=None,
        expected_benefit=ExpectedBenefit(eur=None, summary="No plan to estimate yet."),
        risk=Risk(margin_eur_per_kwh=None, summary="No plan risk margin yet."),
        safety_constraint=SafetyConstraint(
            code=code,
            message=safety_message or summary,
            action=_ACTION_PAUSED,
        ),
        gates=GateOutcomes(
            validator_code=code,
            failsafe=failsafe,
            dwell=dwell,
            cap_reached=cap_reached,
            unconfirmed=unconfirmed,
        ),
        summary=summary,
    )


def build_decision_reason(
    plan: Plan | None,
    *,
    price_by: dict[datetime, float] | None = None,
    validation: Any | None = None,
    plan_reason: str | None = None,
    risk_margin_eur_per_kwh: float | None = None,
    degradation_eur_per_kwh: float = 0.05,
    round_trip_efficiency: float = 0.90,
    decision_outcome: str | None = None,
    failsafe: bool = False,
    unconfirmed: bool = False,
    paused: bool = False,
    summary: str | None = None,
) -> DecisionReason:
    """Assemble the structured reason from the recovered plan + factual gate outcomes.

    `validation` is a `PlanValidation` (or duck-typed equivalent). `decision_outcome` is the
    ModeController preview/decide outcome (`dwell` | `cap_reached` | `unconfirmed` | …).
    When `paused` or validation is unsafe, `safety_constraint.action` is `"paused"` and
    `safety_constraint.code` equals the top unsafe finding code.
    """
    price_by = price_by or {}
    validator_code, validator_message = _top_unsafe_code(validation)
    validation_ok = True
    if validation is not None:
        default_ok = getattr(validation, "status", "") != "unsafe"
        validation_ok = bool(getattr(validation, "ok", default_ok))

    dwell = decision_outcome == "dwell"
    cap_reached = decision_outcome == "cap_reached"
    if decision_outcome == "unconfirmed":
        unconfirmed = True

    is_paused = paused or not validation_ok or failsafe
    if plan is None:
        return empty_decision_reason(
            summary=summary or plan_reason or "No plan is available yet.",
            validator_code=validator_code,
            failsafe=failsafe,
            dwell=dwell,
            cap_reached=cap_reached,
            unconfirmed=unconfirmed,
            safety_message=validator_message,
        )

    chosen = _chosen_window(plan, price_by)
    rejected = _rejected_alternative(plan)
    savings = estimate_daily_savings_eur(
        plan, price_by,
        efficiency=round_trip_efficiency,
        degradation_eur_per_kwh=degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=(
            risk_margin_eur_per_kwh if risk_margin_eur_per_kwh is not None else 0.02
        ),
    )
    benefit = ExpectedBenefit(
        eur=savings,
        summary=(
            f"Estimated net benefit ≈ €{savings:.2f} for this plan."
            if savings > 0
            else "No positive arbitrage benefit estimated for this plan."
        ),
    )
    risk = Risk(
        margin_eur_per_kwh=risk_margin_eur_per_kwh,
        summary=(
            f"Risk margin €{risk_margin_eur_per_kwh:.3f}/kWh applied to break-even."
            if risk_margin_eur_per_kwh is not None
            else "No explicit risk margin configured."
        ),
    )

    if is_paused:
        safety = SafetyConstraint(
            code=validator_code,
            message=validator_message or plan_reason or summary or "Plan paused safely.",
            action=_ACTION_PAUSED,
        )
    else:
        safety = SafetyConstraint(
            code=None,
            message=None,
            action=_ACTION_PROCEED,
        )

    gates = GateOutcomes(
        validator_code=validator_code,
        failsafe=failsafe,
        dwell=dwell,
        cap_reached=cap_reached,
        unconfirmed=unconfirmed,
    )
    text = _homeowner_summary(summary or plan_reason, chosen)
    return DecisionReason(
        chosen_window=chosen,
        rejected_alternative=rejected,
        expected_benefit=benefit,
        risk=risk,
        safety_constraint=safety,
        gates=gates,
        summary=text,
    )
