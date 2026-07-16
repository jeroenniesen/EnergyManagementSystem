"""Alerts + per-plan data-quality badge (SPEC §9.3, §8.11). Pure functions over the current
freshness snapshot + dry-run + the controller's decision outcome — easy to test, no I/O.

Every alert answers four questions (BACKLOG B-37, "calm actionable warnings"): what happened
(`message`), is my home/battery safe (`safe`), and what can I do about it (`action` — "nothing
needed, EMS handles this automatically" is a complete, honest answer, not a cop-out). No alert may
describe a condition without a next step."""
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


# Emotionally-complete signal messages (emotional review #5): say what's wrong, whether the battery
# is safe, and what degrades. Critical signals (grid/soc) block control; the rest just degrade a
# feature. {state} is filled with "unavailable" (missing) or "delayed" (stale).
_SIGNAL_INFO: dict[str, dict[str, str]] = {
    "grid": {
        "message": "Grid meter {state} — EMS can't see your usage, so it's holding the battery "
                    "in safe mode (the battery is safe).",
        "safe": "Yes — the battery holds its current safe mode; nothing changes until EMS can "
                "see your grid meter again.",
        "action": "Nothing needed — EMS retries automatically. If this lasts past an hour, check "
                   "the HomeWizard P1 meter's connection in Home Assistant.",
    },
    "soc": {
        "message": "Battery level {state} — EMS is holding safe mode until it returns (the "
                    "battery is safe).",
        "safe": "Yes — EMS won't act blind; the battery stays in its current safe mode until the "
                "reading returns.",
        "action": "Nothing needed — EMS retries automatically. If this lasts past an hour, check "
                   "the battery's connection in Home Assistant.",
    },
    "solar": {
        "message": "Solar reading {state} — solar accounting is less precise; the battery is "
                    "unaffected.",
        "safe": "Yes — this only affects solar accounting, not battery safety or control.",
        "action": "Nothing needed — EMS keeps controlling the battery normally. If it persists, "
                   "check the HomeWizard solar meter in Home Assistant.",
    },
    "ev": {
        "message": "Car meter {state} — the car-protection guard is paused; the battery is "
                    "unaffected.",
        "safe": "Yes — the battery itself is unaffected; only the car-charging guard is paused.",
        "action": "Nothing needed — the guard resumes once the car meter reports again. Check "
                   "the HomeWizard car meter if this continues for hours.",
    },
    "battery": {
        "message": "Battery power reading {state} — SoC-based decisions may lag; the battery is "
                    "safe.",
        "safe": "Yes — the battery keeps running; only fine-grained SoC-based decisions may lag "
                "briefly.",
        "action": "Nothing needed — EMS catches up automatically once readings resume. Check the "
                   "battery's connection in Home Assistant if this persists.",
    },
}
_STATE_WORD = {"missing": "unavailable", "stale": "delayed"}
# Fallback copy for any signal key not covered above, so a new signal never ships without an
# answer to "is my home safe" / "what can I do" (B-37: no condition-without-a-next-step warnings).
_DEFAULT_SIGNAL_SAFE = (
    "Yes — EMS falls back to its own safe mode whenever a signal it depends on is missing."
)
_DEFAULT_SIGNAL_ACTION = (
    "Nothing needed — EMS retries automatically. Check Home Assistant if this persists past an "
    "hour."
)


def derive_alerts(
    freshness: dict[str, str],
    *,
    dry_run: bool,
    decision_outcome: str | None,
) -> list[Alert]:
    alerts: list[Alert] = []
    if dry_run:
        alerts.append(Alert(
            "dry_run_active", "info",
            "Watch-only mode — EMS observes and advises but won't change the battery.",
            safe="Yes — in this mode EMS only observes; it never writes to the battery.",
            action="Nothing needed — this is expected while you're evaluating EMS. Turn off "
                   "dry-run in Manage → Settings when you're ready for it to act.",
        ))
    for sig, state in sorted(freshness.items()):
        if state in ("missing", "stale"):
            sev = "critical" if sig in CRITICAL_SIGNALS else "warning"
            info = _SIGNAL_INFO.get(sig, {})
            msg_tpl = info.get("message", f"{sig} signal {{state}}")
            msg = msg_tpl.format(state=_STATE_WORD.get(state, state))
            alerts.append(Alert(
                f"{sig}_{state}", sev, msg,
                safe=info.get("safe", _DEFAULT_SIGNAL_SAFE),
                action=info.get("action", _DEFAULT_SIGNAL_ACTION),
            ))
    if decision_outcome == "failed_unrecovered":
        alerts.append(Alert(
            "battery_write_failed_unrecovered", "critical",
            "A battery command AND the safe-mode recovery were both unconfirmed — this needs "
            "attention; check the battery connection.",
            safe="The battery keeps its last confirmed mode — it isn't stuck mid-command, but "
                 "EMS also couldn't confirm the safe-mode fallback.",
            action="Check the battery's power and network connection now; if it doesn't recover, "
                   "restart the Indevolt gateway or check the Indevolt app.",
        ))
    elif decision_outcome == "failed_recovered":
        alerts.append(Alert(
            "battery_write_failed_recovered", "warning",
            "A battery command didn't confirm, so EMS reverted to safe mode (self-use). No "
            "action needed.",
            safe="Yes — the battery reverted to its own self-use mode, which is always safe.",
            action="Nothing needed — EMS will retry the change on its own. Only check the "
                   "battery's connection if this keeps happening.",
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
