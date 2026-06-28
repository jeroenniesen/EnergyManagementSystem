"""Layered readiness (energy review #7/#10): a control system should not answer one boolean
"ready?". It should distinguish

  - alive          — the process is up and serving,
  - dashboard_ready— it can serve the UI + history (store reachable),
  - sensing_ready  — the critical signals (grid + SoC) are fresh, so load/SoC are trustworthy,
  - planning_ready — there's a plan from non-unsafe inputs,
  - control_ready  — AND it's armed (operational, not dry-run) AND the plan validates AND the
                     battery capability is known — i.e. it is actually safe to command the battery.

The `summary` is a calm, homeowner-facing sentence (emotional review: "The battery is safe; EMS is
only watching." / "Control is blocked until battery data returns." / "Live control is ready.").
Pure + unit-tested; the web layer feeds it the booleans it already computes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Readiness:
    alive: bool
    dashboard_ready: bool
    sensing_ready: bool
    planning_ready: bool
    control_ready: bool
    summary: str

    def to_dict(self) -> dict:
        return {
            "alive": self.alive,
            "dashboard_ready": self.dashboard_ready,
            "sensing_ready": self.sensing_ready,
            "planning_ready": self.planning_ready,
            "control_ready": self.control_ready,
            "summary": self.summary,
        }


def compute_readiness(
    *,
    store_ok: bool,
    sensing_ok: bool,
    plan_ok: bool,
    data_quality: str,
    plan_valid: bool,
    operational: bool,
    capability_ok: bool,
) -> Readiness:
    """Derive the readiness layers (each implies the ones above it) + a calm summary sentence."""
    alive = True
    dashboard_ready = store_ok
    sensing_ready = dashboard_ready and sensing_ok
    planning_ready = sensing_ready and plan_ok and data_quality != "unsafe"
    control_ready = planning_ready and operational and plan_valid and capability_ok

    if not dashboard_ready:
        summary = "Starting up — the dashboard isn't ready yet."
    elif not sensing_ready:
        summary = ("Needs attention — battery or meter data is unavailable. The battery is safe "
                   "and managing itself; EMS is not controlling.")
    elif not operational:
        # The common, intended state for this install: observing, battery on its own self-use.
        summary = "All good — the battery is safe and EMS is watching only (it won't take control)."
    elif control_ready:
        summary = "Live control is ready — EMS will follow the validated plan."
    else:
        summary = ("Control is paused safely — the plan or battery check hasn't passed yet, so EMS "
                   "is holding self-consumption.")

    return Readiness(
        alive=alive, dashboard_ready=dashboard_ready, sensing_ready=sensing_ready,
        planning_ready=planning_ready, control_ready=control_ready, summary=summary,
    )


_ACTION_PHRASE = {
    "grid_charge_to_target": "topping up the battery",
    "discharge_for_load": "running the house on your battery",
    "hold_reserve": "holding the battery for later",
    "allow_self_consumption": "the battery is running the house",
}


def home_state(
    readiness: Readiness, *, intent: str | None, override_active: bool, simulated: bool = False,
) -> dict:
    """The single top-of-dashboard headline + tone the homeowner reads first (emotional review #1):
    answers safe / watching / controlling / needs-attention. `tone` ∈
    good | watching | controlling | attention. In observe-only (not control_ready) mode EMS is
    'watching' — it never claims to be controlling the battery."""
    if not readiness.sensing_ready:
        return {"headline": "Needs attention — battery or meter data is unavailable",
                "tone": "attention", "simulated": simulated}
    if override_active:
        return {"headline": "You're in manual control", "tone": "controlling",
                "simulated": simulated}
    phrase = _ACTION_PHRASE.get(intent or "", "the battery is safe")
    if readiness.control_ready:
        if intent == "allow_self_consumption":
            return {"headline": f"All good — {phrase}", "tone": "good", "simulated": simulated}
        return {"headline": phrase[:1].upper() + phrase[1:], "tone": "controlling",
                "simulated": simulated}
    # Observe-only: EMS watches and advises; the battery runs on its own.
    return {"headline": f"Watching — {phrase}", "tone": "watching", "simulated": simulated}
