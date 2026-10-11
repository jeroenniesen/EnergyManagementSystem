"""Read-only 'what EMS may do' snapshot for Settings (#246 / B-35 slice 1).

Curates existing settings + effective write floors into homeowner-facing rows.
Never writes settings or commands the battery.
"""
from __future__ import annotations

from typing import Any

from ems.connection import watching_only_primary_reason
from ems.domain import CapabilityReport
from ems.settings import GRID_FUSE_PRESET_W, SETTINGS_BY_KEY


def _num(settings: dict[str, Any], key: str) -> float | None:
    raw = settings.get(key, SETTINGS_BY_KEY[key].default if key in SETTINGS_BY_KEY else None)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _bound(
    *,
    id: str,
    label: str,
    explanation: str,
    status: str,
    display: str,
    value: Any = None,
    unit: str = "",
    setting_key: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "label": label,
        "explanation": explanation,
        "status": status,  # known | unknown | effective
        "display": display,
        "value": value,
        "unit": unit,
        "setting_key": setting_key,
    }


def build_automation_bounds(
    settings: dict[str, Any],
    *,
    dry_run: bool,
    config_dry_run: bool,
    dev_mode: str,
    capability: CapabilityReport | None = None,
    lifecycle_state: str | None = None,
) -> dict[str, Any]:
    """Assemble the read-only bounds payload for GET /api/automation-bounds."""
    cause, reason = watching_only_primary_reason(
        settings,
        config_dry_run=config_dry_run,
        dry_run=dry_run,
        dev_mode=dev_mode,
        lifecycle_state=lifecycle_state,
    )
    writes_allowed = not dry_run and cause is None

    watch = bool(settings.get("control.dry_run", True))
    operational = bool(settings.get("control.operational", False))
    export_armed = bool(settings.get("control.allow_export_discharge", False))

    bounds: list[dict[str, Any]] = []

    # --- Effective write permission (runtime floors, not a single setting) -------------------
    if writes_allowed:
        bounds.append(_bound(
            id="battery_writes",
            label="Battery writes",
            explanation="EMS may change the battery mode when the plan says so.",
            status="effective",
            display="Allowed",
            value=True,
        ))
    else:
        bounds.append(_bound(
            id="battery_writes",
            label="Battery writes",
            explanation=reason or "Battery writes are currently disabled.",
            status="effective",
            display="Blocked",
            value=False,
        ))

    bounds.append(_bound(
        id="watch_only",
        label="Watch only",
        explanation=(
            "When on, EMS plans and explains but does not write to the battery. "
            "Config or mock mode can still force watch-only even if this is off."
        ),
        status="known",
        display="On" if watch else "Off",
        value=watch,
        setting_key="control.dry_run",
    ))
    bounds.append(_bound(
        id="operational",
        label="Let the system control the battery",
        explanation=(
            "Master arming switch. Off means EMS stays unarmed even when Watch only is off."
        ),
        status="known",
        display="On" if operational else "Off",
        value=operational,
        setting_key="control.operational",
    ))

    # --- Reserve / power --------------------------------------------------------------------
    min_reserve = _num(settings, "battery.min_reserve_soc")
    bounds.append(_bound(
        id="min_reserve_soc",
        label="Minimum battery reserve",
        explanation="EMS will not plan to empty the battery below this state of charge.",
        status="known" if min_reserve is not None else "unknown",
        display=f"{min_reserve:.0f}%" if min_reserve is not None else "Unknown",
        value=min_reserve,
        unit="%",
        setting_key="battery.min_reserve_soc",
    ))

    night_reserve = _num(settings, "battery.night_reserve_kwh")
    bounds.append(_bound(
        id="night_reserve_kwh",
        label="Night reserve",
        explanation="Energy kept aside so the house can run overnight on the battery.",
        status="known" if night_reserve is not None else "unknown",
        display=f"{night_reserve:.1f} kWh" if night_reserve is not None else "Unknown",
        value=night_reserve,
        unit="kWh",
        setting_key="battery.night_reserve_kwh",
    ))

    for key, id_, label, explain in (
        (
            "battery.max_charge_w",
            "max_charge_w",
            "Max charge power",
            "Upper limit when EMS asks the battery to charge from the grid.",
        ),
        (
            "battery.max_discharge_w",
            "max_discharge_w",
            "Max discharge power",
            "Upper limit when EMS asks the battery to discharge for the house or export.",
        ),
    ):
        settings_w = _num(settings, key)
        cap_w = None
        if capability is not None:
            cap_w = (
                float(capability.max_charge_w) if id_ == "max_charge_w"
                else float(capability.max_discharge_w)
            )
        if settings_w is None and cap_w is None:
            bounds.append(_bound(
                id=id_, label=label, explanation=explain,
                status="unknown", display="Unknown", setting_key=key,
            ))
        elif cap_w is None:
            bounds.append(_bound(
                id=id_, label=label,
                explanation=f"{explain} Device limit not probed — showing your setting only.",
                status="known",
                display=f"{settings_w:.0f} W",
                value=settings_w,
                unit="W",
                setting_key=key,
            ))
        elif settings_w is None:
            bounds.append(_bound(
                id=id_, label=label,
                explanation=f"{explain} From the battery probe.",
                status="known",
                display=f"{cap_w:.0f} W",
                value=cap_w,
                unit="W",
                setting_key=key,
            ))
        else:
            effective = min(settings_w, cap_w)
            bounds.append(_bound(
                id=id_, label=label,
                explanation=(
                    f"{explain} Effective ceiling is the lower of your setting "
                    f"({settings_w:.0f} W) and the device ({cap_w:.0f} W)."
                ),
                status="effective",
                display=f"{effective:.0f} W",
                value=effective,
                unit="W",
                setting_key=key,
            ))

    # --- Grid fuse --------------------------------------------------------------------------
    fuse = str(settings.get("control.grid_fuse", "unset") or "unset")
    limit_w = _num(settings, "control.grid_limit_w")
    if fuse == "unset":
        bounds.append(_bound(
            id="grid_fuse",
            label="Main fuse",
            explanation=(
                "Not confirmed yet. EMS keeps a conservative ceiling until you choose "
                "your real main fuse in Settings."
            ),
            status="unknown",
            display="Not confirmed",
            value=None,
            setting_key="control.grid_fuse",
        ))
        # Still show the watt ceiling in use, honestly tagged.
        ceiling = limit_w if limit_w is not None else 5750.0
        bounds.append(_bound(
            id="grid_limit_w",
            label="Grid charge ceiling (while fuse unset)",
            explanation=(
                "Temporary watt limit used until the main fuse is confirmed. "
                "Not your real fuse rating."
            ),
            status="effective",
            display=f"{ceiling:.0f} W",
            value=ceiling,
            unit="W",
            setting_key="control.grid_limit_w",
        ))
    else:
        fuse_label = {
            "1x25": "1×25 A", "1x35": "1×35 A", "3x25": "3×25 A", "3x35": "3×35 A",
            "custom": "Custom",
        }.get(fuse, fuse)
        preset_w = GRID_FUSE_PRESET_W.get(fuse)
        display_fuse = fuse_label if preset_w is None else f"{fuse_label} (≈ {preset_w:.0f} W)"
        bounds.append(_bound(
            id="grid_fuse",
            label="Main fuse",
            explanation="House main fuse. EMS will not plan grid-charge above this ceiling.",
            status="known",
            display=display_fuse,
            value=fuse,
            setting_key="control.grid_fuse",
        ))
        if limit_w is not None and limit_w <= 0:
            bounds.append(_bound(
                id="grid_limit_w",
                label="Grid charge ceiling",
                explanation="Fuse check disabled (0 W means no watt ceiling from the fuse).",
                status="known",
                display="Disabled",
                value=0.0,
                unit="W",
                setting_key="control.grid_limit_w",
            ))
        elif limit_w is not None:
            bounds.append(_bound(
                id="grid_limit_w",
                label="Grid charge ceiling",
                explanation="Maximum grid-charge power EMS will plan against the main fuse.",
                status="known",
                display=f"{limit_w:.0f} W",
                value=limit_w,
                unit="W",
                setting_key="control.grid_limit_w",
            ))
        else:
            bounds.append(_bound(
                id="grid_limit_w",
                label="Grid charge ceiling",
                explanation="Grid watt ceiling is not available.",
                status="unknown",
                display="Unknown",
                setting_key="control.grid_limit_w",
            ))

    # --- Mode thrash guards -----------------------------------------------------------------
    switches = _num(settings, "control.max_switches_per_day")
    bounds.append(_bound(
        id="max_switches_per_day",
        label="Max mode changes per day",
        explanation="Soft budget so the battery is not switched too often in one day.",
        status="known" if switches is not None else "unknown",
        display=f"{switches:.0f}" if switches is not None else "Unknown",
        value=switches,
        setting_key="control.max_switches_per_day",
    ))
    dwell = _num(settings, "control.min_dwell_seconds")
    bounds.append(_bound(
        id="min_dwell_seconds",
        label="Minimum time between mode changes",
        explanation="EMS waits at least this long before changing battery mode again.",
        status="known" if dwell is not None else "unknown",
        display=(f"{dwell / 60:.0f} min" if dwell is not None else "Unknown"),
        value=dwell,
        unit="s",
        setting_key="control.min_dwell_seconds",
    ))

    # --- Export arm (Trading) ---------------------------------------------------------------
    bounds.append(_bound(
        id="export_armed",
        label="Sell to the grid (export)",
        explanation=(
            "Forced export to the grid stays off until you arm it on the Trading page. "
            "Off by default."
        ),
        status="known",
        display="Armed" if export_armed else "Not armed",
        value=export_armed,
        setting_key="control.allow_export_discharge",
    ))

    if writes_allowed:
        summary = "EMS may write to the battery within the limits below."
    else:
        summary = reason or "EMS is not writing to the battery right now."

    return {
        "title": "What EMS may do",
        "summary": summary,
        "writes_allowed": writes_allowed,
        "block_reason": reason,
        "block_cause": cause,
        "bounds": bounds,
    }
