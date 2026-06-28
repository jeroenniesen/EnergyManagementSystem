"""Alerts + per-plan data-quality badge (SPEC §9.3, §8.11). Pure functions over the current
freshness snapshot + dry-run + the controller's decision outcome — easy to test, no I/O."""
from __future__ import annotations

from dataclasses import dataclass

# Signals whose staleness/absence makes control unsafe (can't reconstruct load / know SoC).
CRITICAL_SIGNALS = ("grid", "soc")


@dataclass(frozen=True)
class Alert:
    key: str
    severity: str  # info | warning | critical
    message: str


# Emotionally-complete signal messages (emotional review #5): say what's wrong, whether the battery
# is safe, and what degrades. Critical signals (grid/soc) block control; the rest just degrade a
# feature. {state} is filled with "unavailable" (missing) or "delayed" (stale).
_SIGNAL_MSG: dict[str, str] = {
    "grid": "Grid meter {state} — EMS can't see your usage, so it's holding the battery in safe "
            "mode (the battery is safe).",
    "soc": "Battery level {state} — EMS is holding safe mode until it returns (the battery is "
           "safe).",
    "solar": "Solar reading {state} — solar accounting is less precise; the battery is unaffected.",
    "ev": "Car meter {state} — the car-protection guard is paused; the battery is unaffected.",
    "battery": "Battery power reading {state} — SoC-based decisions may lag; the battery is safe.",
}
_STATE_WORD = {"missing": "unavailable", "stale": "delayed"}


def derive_alerts(
    freshness: dict[str, str],
    *,
    dry_run: bool,
    decision_outcome: str | None,
) -> list[Alert]:
    alerts: list[Alert] = []
    if dry_run:
        alerts.append(Alert("dry_run_active", "info",
                            "Watch-only mode — EMS observes and advises but won't change the "
                            "battery."))
    for sig, state in sorted(freshness.items()):
        if state in ("missing", "stale"):
            sev = "critical" if sig in CRITICAL_SIGNALS else "warning"
            msg = _SIGNAL_MSG.get(sig, f"{sig} signal {{state}}").format(
                state=_STATE_WORD.get(state, state))
            alerts.append(Alert(f"{sig}_{state}", sev, msg))
    if decision_outcome == "failed_unrecovered":
        alerts.append(Alert("battery_write_failed_unrecovered", "critical",
                            "A battery command AND the safe-mode recovery were both unconfirmed — "
                            "this needs attention; check the battery connection."))
    elif decision_outcome == "failed_recovered":
        alerts.append(Alert("battery_write_failed_recovered", "warning",
                            "A battery command didn't confirm, so EMS reverted to safe mode "
                            "(self-use). No action needed."))
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
