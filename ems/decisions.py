"""Map raw audit-log rows into homeowner-facing decision-timeline events (2026-07-15 dashboard
plan). Pure: no I/O — the `/api/decisions` endpoint just feeds it `audit_store` rows. Each event
answers the drawer's questions in plain language: what happened, why, the consequence, and whether
the homeowner needs to act.

Only battery-control rows become events (`battery_decision`, `shutdown_restore`); config changes,
overrides and the like are surfaced elsewhere and are skipped here."""
from __future__ import annotations

from typing import Any

_NO_ACTION = "No action needed."
_SAFE_BASELINE = "The safe baseline (self-consumption) remains active."

# Homeowner words for a physical battery mode.
_MODE_WORD = {
    "charge": "charging",
    "discharge": "powering the house",
    "idle": "standby",
    "auto": "safe self-consumption",
}


def _mode_word(mode: Any) -> str:
    return _MODE_WORD.get(str(mode), str(mode or "the plan"))


def _from_battery_decision(base: dict, detail: dict) -> dict:
    outcome = detail.get("outcome")
    mode = detail.get("desired_mode")
    word = _mode_word(mode)
    if outcome == "applied":
        return {**base, "title": f"Switched the battery to {word}",
                "consequence": f"The battery is now set to {word}.",
                "action": _NO_ACTION, "severity": "info"}
    if outcome == "economic_skip":
        return {**base, "title": "Skipped trading today",
                "consequence": _SAFE_BASELINE,
                "action": _NO_ACTION, "severity": "info"}
    if outcome in ("cap_reached", "dwell", "not_controlling"):
        return {**base, "title": f"Held — did not switch to {word}",
                "consequence": _SAFE_BASELINE,
                "action": _NO_ACTION, "severity": "info"}
    if outcome == "unconfirmed":
        return {**base, "title": f"Change to {word} is taking effect slowly",
                "consequence": "EMS is holding and re-checking rather than reverting.",
                "action": _NO_ACTION, "severity": "warning"}
    if outcome == "failed_recovered":
        return {**base, "title": f"Couldn't switch to {word} — reverted to safe mode",
                "consequence": _SAFE_BASELINE,
                "action": "Check the battery is reachable if this repeats.", "severity": "warning"}
    if outcome == "failed_unrecovered":
        return {**base, "title": f"Couldn't switch to {word} and couldn't confirm safe mode",
                "consequence": "The battery may be in an unknown state.",
                "action": "Check the battery now.", "severity": "critical"}
    # Watch-only (dry-run) advisory decision: EMS decided what it WOULD do but changed nothing.
    # Render homeowner copy rather than leaking the raw "Would set battery → auto — …" dev summary.
    if detail.get("dry_run") or detail.get("decided_only"):
        title = (f"Would keep the battery on {word}" if mode == "auto"
                 else f"Would set the battery to {word}")
        return {**base, "title": title,
                "consequence": "Watch-only mode — EMS is advising, not changing the battery.",
                "action": _NO_ACTION, "severity": "info"}
    # Truly unknown outcome — a calm generic title (never the raw dev summary).
    return {**base, "title": (f"Battery decision — {word}" if mode else "Battery decision"),
            "consequence": "", "action": _NO_ACTION, "severity": "info"}


def _from_shutdown(base: dict, detail: dict) -> dict:
    confirmed = detail.get("confirmed")
    return {
        **base,
        "title": "Handed the battery back to its safe mode",
        "consequence": ("The battery is on its own safe self-consumption mode."
                        if confirmed else "Restore was not confirmed — verify the device."),
        "action": _NO_ACTION if confirmed else "Check the battery is on its safe mode.",
        "severity": "info" if confirmed else "warning",
    }


def decision_events(rows: list[dict]) -> list[dict]:
    """Turn audit rows (newest-first, as `AuditStore.recent` returns them) into decision events
    `{id, time, title, reason, consequence, action, severity}`. Non-control rows are dropped."""
    out: list[dict] = []
    for r in rows:
        category = r.get("category")
        if category not in ("battery_decision", "shutdown_restore"):
            continue
        detail = r.get("detail") if isinstance(r.get("detail"), dict) else {}
        base = {
            "id": str(r.get("id", r.get("ts", ""))),
            "time": r.get("ts"),
            "reason": detail.get("reason") or r.get("summary") or "",
            "_summary": r.get("summary") or "",
        }
        event = (_from_shutdown(base, detail) if category == "shutdown_restore"
                 else _from_battery_decision(base, detail))
        event.pop("_summary", None)
        out.append(event)
    return out
