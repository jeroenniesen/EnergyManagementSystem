"""Alerts + per-plan data-quality badge (SPEC §9.3, §8.11). Pure functions over the current
freshness snapshot + dry-run + the controller's decision outcome — easy to test, no I/O."""
from __future__ import annotations

from dataclasses import dataclass

# Signals whose staleness/absence makes control unsafe (can't reconstruct load / know SoC).
_CRITICAL_SIGNALS = ("grid", "soc")


@dataclass(frozen=True)
class Alert:
    key: str
    severity: str  # info | warning | critical
    message: str


def derive_alerts(
    freshness: dict[str, str],
    *,
    dry_run: bool,
    decision_outcome: str | None,
) -> list[Alert]:
    alerts: list[Alert] = []
    if dry_run:
        alerts.append(Alert("dry_run_active", "info", "Dry-run: no battery writes"))
    for sig, state in sorted(freshness.items()):
        if state == "missing":
            sev = "critical" if sig in _CRITICAL_SIGNALS else "warning"
            alerts.append(Alert(f"{sig}_missing", sev, f"{sig} signal missing"))
        elif state == "stale":
            sev = "critical" if sig in _CRITICAL_SIGNALS else "warning"
            alerts.append(Alert(f"{sig}_stale", sev, f"{sig} signal stale"))
    if decision_outcome == "failed_unrecovered":
        alerts.append(Alert("battery_write_failed", "critical",
                            "Battery write AND AUTO recovery both unconfirmed"))
    elif decision_outcome == "failed_recovered":
        alerts.append(Alert("battery_write_failed", "warning",
                            "Battery write unconfirmed; reverted to AUTO"))
    return alerts


def data_quality(freshness: dict[str, str], *, prices_ok: bool, forecast_ok: bool) -> str:
    """complete | degraded | price_fallback | unsafe (SPEC §8.11)."""
    for sig in _CRITICAL_SIGNALS:
        if freshness.get(sig, "missing") != "fresh":
            return "unsafe"  # can't safely reconstruct/plan
    if not prices_ok:
        return "price_fallback"
    if not forecast_ok or any(state != "fresh" for state in freshness.values()):
        return "degraded"
    return "complete"
