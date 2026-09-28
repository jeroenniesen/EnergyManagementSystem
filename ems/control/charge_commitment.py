"""Restart-/deploy-safe GRID_CHARGE commitment (#177).

When EMS applies a deliberate `GRID_CHARGE_TO_TARGET`, the active contract (target SoC +
deadline + reason) is persisted with control state so a graceful `shutdown_restore` → AUTO
(#127) cannot *silently* erase the cheap-window commitment. On the next boot the runtime
either **resumes** the charge (still valid, armed, not dry-run, data not unsafe) or
**aborts with an explicit audited reason** (expired / target reached / dry_run / unarmed /
unsafe). Fail-safe never worsens "no EMS": uncertain or unarmed ⇒ no write + clear reason.

Pure data + evaluation — persistence lives in `ModeController` / `ControlStateStore`;
writes still go only through `decide()` / the battery writer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ems.domain import BatteryIntent

# Outcomes for evaluate():
#   none   — no stored commitment
#   resume — still valid; re-apply CHARGE after restart
#   abort  — clear the commitment; reason is operator-visible
#   hold   — keep stored; not ready to resume yet (e.g. still in startup grace)
CommitmentAction = Literal["none", "resume", "abort", "hold"]

# Explicit abort/complete codes — never silent wipe (issue #177).
ABORT_EXPIRED = "commitment_aborted: expired"
ABORT_TARGET_REACHED = "commitment_completed: target_soc_reached"
ABORT_DRY_RUN = "commitment_aborted: dry_run"
ABORT_UNARMED = "commitment_aborted: unarmed"
ABORT_UNSAFE = "commitment_aborted: unsafe"
ABORT_SHUTDOWN_PRESERVED = "commitment_preserved: shutdown_restore"  # audit-only on stop


@dataclass(frozen=True)
class ChargeCommitment:
    """An applied GRID_CHARGE contract that must survive restart."""

    target_soc: float
    deadline: datetime  # tz-aware UTC (or any aware tz)
    reason: str
    applied_at: datetime
    power_w: float | None = None
    intent: str = BatteryIntent.GRID_CHARGE_TO_TARGET.value

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "target_soc": self.target_soc,
            "deadline": self.deadline.isoformat(),
            "reason": self.reason,
            "applied_at": self.applied_at.isoformat(),
            "power_w": self.power_w,
        }

    @staticmethod
    def from_dict(raw: object) -> ChargeCommitment | None:
        """Rebuild from persisted JSON; corrupt/legacy → None (caller treats as absent)."""
        if not isinstance(raw, dict) or not raw:
            return None
        try:
            intent = str(raw.get("intent") or BatteryIntent.GRID_CHARGE_TO_TARGET.value)
            if intent != BatteryIntent.GRID_CHARGE_TO_TARGET.value:
                return None
            target = float(raw["target_soc"])
            deadline = datetime.fromisoformat(str(raw["deadline"]))
            applied_at = datetime.fromisoformat(str(raw["applied_at"]))
            reason = str(raw.get("reason") or "grid charge commitment")
            power_raw = raw.get("power_w")
            power_w = float(power_raw) if power_raw is not None else None
        except (KeyError, TypeError, ValueError):
            return None
        if deadline.tzinfo is None or applied_at.tzinfo is None:
            return None
        if not (0.0 < target <= 100.0):
            return None
        return ChargeCommitment(
            target_soc=target,
            deadline=deadline,
            reason=reason,
            applied_at=applied_at,
            power_w=power_w,
            intent=intent,
        )


@dataclass(frozen=True)
class CommitmentVerdict:
    action: CommitmentAction
    reason: str
    commitment: ChargeCommitment | None = None


def evaluate_commitment(
    commitment: ChargeCommitment | None,
    *,
    now: datetime,
    soc_pct: float | None,
    dry_run: bool,
    armed: bool,
    data_quality: str,
    grace_elapsed: bool,
) -> CommitmentVerdict:
    """Decide resume / abort / hold for a persisted charge commitment.

    Fail-safe order: dry_run / unarmed / unsafe abort without writing; expired or target
    reached clear the contract; only a fully valid, post-grace, live+armed path resumes.
    """
    if commitment is None:
        return CommitmentVerdict("none", "no active charge commitment")

    if now >= commitment.deadline:
        return CommitmentVerdict(
            "abort",
            f"{ABORT_EXPIRED} — deadline {commitment.deadline.isoformat()} passed",
            commitment,
        )

    if soc_pct is not None and soc_pct >= commitment.target_soc:
        return CommitmentVerdict(
            "abort",
            f"{ABORT_TARGET_REACHED} — SoC {soc_pct:.0f}% ≥ target {commitment.target_soc:.0f}%",
            commitment,
        )

    if dry_run:
        return CommitmentVerdict(
            "abort",
            (f"{ABORT_DRY_RUN} — would have resumed GRID_CHARGE to "
             f"{commitment.target_soc:.0f}% by {commitment.deadline.isoformat()}"),
            commitment,
        )

    if not armed:
        return CommitmentVerdict(
            "abort",
            f"{ABORT_UNARMED} — driver not armed; cannot resume GRID_CHARGE",
            commitment,
        )

    if data_quality == "unsafe":
        return CommitmentVerdict(
            "abort",
            f"{ABORT_UNSAFE} — data quality unsafe; fail-safe AUTO (commitment cleared)",
            commitment,
        )

    if not grace_elapsed:
        return CommitmentVerdict(
            "hold",
            "charge commitment held during startup grace (observe only)",
            commitment,
        )

    return CommitmentVerdict(
        "resume",
        (f"resuming GRID_CHARGE commitment to {commitment.target_soc:.0f}% "
         f"by {commitment.deadline.isoformat()} — {commitment.reason}"),
        commitment,
    )


def shutdown_preserve_reason(commitment: ChargeCommitment | None) -> str | None:
    """Human-readable audit line when #127 shutdown_restore runs while a commitment is active."""
    if commitment is None:
        return None
    return (
        f"{ABORT_SHUTDOWN_PRESERVED} — battery restored to AUTO; "
        f"GRID_CHARGE to {commitment.target_soc:.0f}% by "
        f"{commitment.deadline.isoformat()} kept for resume on next boot"
    )
