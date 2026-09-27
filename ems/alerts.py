"""Alerts + per-plan data-quality badge (SPEC §9.3, §8.11). Pure functions over the current
freshness snapshot + dry-run + the controller's decision outcome — easy to test, no I/O.

Every alert answers four questions (BACKLOG B-37 / B-09): what happened (`message`), is my
home/battery safe (`safe`), what EMS is doing (`ems_doing`), and what I can do (`action` —
"nothing needed, EMS handles this automatically" is a complete, honest answer, not a cop-out).
No alert may describe a condition without a next step.

Copy rules (B-09 / issue #73):
- Name the real device (P1 meter, Indevolt battery) — never "check in Home Assistant".
- Never promise more than failsafe guarantees: no "the battery is safe" / "nothing changes"
  without a confirmed AUTO. Stale/missing critical signals without confirmed AUTO must not
  say "safe mode".
"""
from __future__ import annotations

from dataclasses import dataclass

# Signals whose staleness/absence makes control unsafe (can't reconstruct load / know SoC).
CRITICAL_SIGNALS = ("grid", "soc")


@dataclass(frozen=True)
class Alert:
    key: str
    severity: str  # info | warning | critical
    message: str
    safe: str  # is-my-home/battery-safe answer, plain language, no jargon, no blame
    action: str  # the one thing the user can do — an automatic-behaviour reassurance counts
    ems_doing: str  # what EMS is doing / will do in this situation (failsafe intent)


# Emotionally-complete signal messages (B-09): say what's wrong, what EMS does, and what
# degrades — without claiming a confirmed AUTO "safe mode" unless the caller says so.
# {state} is filled with "unavailable" (missing) or "delayed" (stale).
_SIGNAL_INFO: dict[str, dict[str, str]] = {
    "grid": {
        "message": "P1 meter {state} — EMS can't see your grid usage, so it pauses new battery "
                    "commands until the meter returns.",
        "safe": "EMS is not claiming a live battery mode — only that it stops new commands "
                "until the P1 meter is back.",
        "ems_doing": "EMS holds off on new battery commands and retries the P1 meter "
                     "automatically.",
        "action": "Nothing needed — EMS retries automatically. If this lasts past an hour, "
                   "check the HomeWizard P1 meter's power and network.",
        # When AUTO is confirmed, `safe` may mention self-use; still avoid "the battery is safe".
        "safe_confirmed_auto": "EMS has confirmed the battery is in its own self-use mode "
                               "while the P1 meter is {state}.",
    },
    "soc": {
        "message": "Battery level {state} — EMS won't plan blind, so it pauses new battery "
                    "commands until the Indevolt reading returns.",
        "safe": "EMS is not claiming a live battery mode — only that it won't command a new "
                "mode without a battery-level reading.",
        "ems_doing": "EMS holds off on new battery commands and retries the Indevolt battery "
                     "automatically.",
        "action": "Nothing needed — EMS retries automatically. If this lasts past an hour, "
                   "check the Indevolt battery's power and network.",
        "safe_confirmed_auto": "EMS has confirmed the battery is in its own self-use mode "
                               "while the battery level is {state}.",
    },
    "solar": {
        "message": "Solar meter {state} — solar accounting is less precise; battery control "
                    "continues.",
        "safe": "This only affects solar accounting, not whether EMS can command the battery.",
        "ems_doing": "EMS keeps controlling the battery; only solar totals may be less precise.",
        "action": "Nothing needed — EMS keeps controlling the battery normally. If it persists, "
                   "check the HomeWizard solar meter's power and network.",
    },
    "ev": {
        "message": "Car meter {state} — the car-protection guard is paused; battery control "
                    "continues.",
        "safe": "The battery itself is unaffected; only the car-charging guard is paused.",
        "ems_doing": "EMS keeps the normal battery plan; the car-charging guard resumes when "
                     "the meter reports again.",
        "action": "Nothing needed — the guard resumes once the car meter reports again. Check "
                   "the HomeWizard car meter's power and network if this continues for hours.",
    },
    "battery": {
        "message": "Battery power reading {state} — fine-grained SoC decisions may lag.",
        "safe": "EMS still has the last known mode; only fine-grained power-based decisions "
                "may lag briefly.",
        "ems_doing": "EMS keeps the current plan and catches up once Indevolt readings resume.",
        "action": "Nothing needed — EMS catches up automatically once readings resume. Check "
                   "the Indevolt battery's power and network if this persists.",
    },
}
_STATE_WORD = {"missing": "unavailable", "stale": "delayed"}
# Fallback copy for any signal key not covered above, so a new signal never ships without an
# answer to "is my home safe" / "what is EMS doing" / "what can I do" (B-37/B-09).
_DEFAULT_SIGNAL_SAFE = (
    "EMS falls back to holding new commands whenever a signal it depends on is missing."
)
_DEFAULT_SIGNAL_EMS_DOING = (
    "EMS retries the missing signal automatically and avoids new battery commands it can't "
    "justify."
)
_DEFAULT_SIGNAL_ACTION = (
    "Nothing needed — EMS retries automatically. Check the affected meter's or battery's "
    "power and network if this persists past an hour."
)


def derive_alerts(
    freshness: dict[str, str],
    *,
    dry_run: bool,
    decision_outcome: str | None,
    confirmed_auto: bool = False,
    price_horizon_ok: bool | None = None,
    control_overrun: bool = False,
) -> list[Alert]:
    """Build the calm alert list for the current snapshot.

    `confirmed_auto`: True only when the controller's last confirmed action is AUTO and not
    unconfirmed — gates wording that would otherwise over-claim failsafe ("safe mode").
    `price_horizon_ok`: None = unknown/not yet checked (no alert); False = incomplete horizon.
    `control_overrun`: True while a recent control-cycle overrun is latched.
    """
    alerts: list[Alert] = []
    if dry_run:
        alerts.append(Alert(
            "dry_run_active", "info",
            "Watch-only mode — EMS observes and advises but won't change the battery.",
            safe="In this mode EMS only observes; it never writes to the battery.",
            action="Nothing needed — this is expected while you're evaluating EMS. Turn off "
                   "dry-run in Manage → Settings when you're ready for it to act.",
            ems_doing="EMS watches meters and the Indevolt battery but issues no mode changes.",
        ))
    for sig, state in sorted(freshness.items()):
        if state in ("missing", "stale"):
            sev = "critical" if sig in CRITICAL_SIGNALS else "warning"
            info = _SIGNAL_INFO.get(sig, {})
            state_word = _STATE_WORD.get(state, state)
            msg_tpl = info.get("message", f"{sig} signal {{state}}")
            msg = msg_tpl.format(state=state_word)
            if confirmed_auto and "safe_confirmed_auto" in info:
                safe = info["safe_confirmed_auto"].format(state=state_word)
            else:
                safe = info.get("safe", _DEFAULT_SIGNAL_SAFE)
            alerts.append(Alert(
                f"{sig}_{state}", sev, msg,
                safe=safe,
                action=info.get("action", _DEFAULT_SIGNAL_ACTION),
                ems_doing=info.get("ems_doing", _DEFAULT_SIGNAL_EMS_DOING),
            ))
    if decision_outcome == "failed_unrecovered":
        alerts.append(Alert(
            "battery_write_failed_unrecovered", "critical",
            "A battery command AND the self-use recovery were both unconfirmed — this needs "
            "attention; check the Indevolt battery connection.",
            safe="The battery keeps its last confirmed mode — it isn't stuck mid-command, but "
                 "EMS also couldn't confirm the self-use fallback.",
            action="Check the Indevolt battery's power and network now; if it doesn't recover, "
                   "restart the Indevolt gateway or check the Indevolt app.",
            ems_doing="EMS stopped issuing new commands until it can confirm the battery again.",
        ))
    elif decision_outcome == "failed_recovered":
        alerts.append(Alert(
            "battery_write_failed_recovered", "warning",
            "A battery command didn't confirm, so EMS reverted to the battery's own self-use. "
            "No action needed.",
            safe="EMS confirmed the battery returned to its own self-use mode after the failed "
                 "command.",
            action="Nothing needed — EMS will retry the change on its own. Only check the "
                   "Indevolt battery's connection if this keeps happening.",
            ems_doing="EMS commanded self-use after the failed write and will retry later.",
        ))
    elif decision_outcome == "unconfirmed":
        alerts.append(Alert(
            "battery_command_unconfirmed", "warning",
            "A battery command was not confirmed — the Indevolt battery may still be catching "
            "up.",
            safe="EMS is not assuming a new mode until the battery confirms; the last "
                 "confirmed mode still applies.",
            action="Nothing needed — EMS retries automatically. If this keeps happening, check "
                   "the Indevolt battery's power and network.",
            ems_doing="EMS holds and will retry; it does not pile on more commands while this "
                      "is open.",
        ))
    if price_horizon_ok is False:
        alerts.append(Alert(
            "price_horizon_incomplete", "warning",
            "Price horizon incomplete — EMS can't plan the full day on Tibber prices yet.",
            safe="EMS will not run price-based battery moves until a complete price day is "
                 "available.",
            action="Nothing needed — prices refresh automatically. If this lasts into the "
                   "evening, check your Tibber connection in Settings.",
            ems_doing="EMS keeps self-use planning and waits for a full Tibber price horizon.",
        ))
    if control_overrun:
        alerts.append(Alert(
            "control_overrun", "warning",
            "A control cycle ran too long — EMS interrupted it to protect the plan.",
            safe="EMS tries to return the battery to its own self-use after an overrun; "
                 "confirmation comes on the next successful cycle.",
            action="Nothing needed — EMS recovers automatically. If this keeps recurring, "
                   "check the Indevolt battery's network latency.",
            ems_doing="EMS stopped the slow cycle and commands the battery back to self-use.",
        ))
    return alerts


def data_quality(freshness: dict[str, str], *, prices_ok: bool, forecast_ok: bool) -> str:
    """complete | degraded | price_fallback | unsafe (SPEC §8.11).

    Precedence (most severe first): unsafe > price_fallback > degraded > complete. So a missing
    price with a simultaneously-stale non-critical signal reports price_fallback (the per-signal
    staleness still surfaces separately as an alert)."""
    for sig in CRITICAL_SIGNALS:
        if freshness.get(sig, "missing") != "fresh":
            return "unsafe"  # can't safely reconstruct/plan
    if not prices_ok:
        return "price_fallback"
    if not forecast_ok or any(state != "fresh" for state in freshness.values()):
        return "degraded"
    return "complete"
