"""UI-editable runtime settings (SPEC §9.4): a schema + validation + effective-config overlay.

`config.yaml` holds read-only **defaults**; these settings live in the runtime store (/data) and
overlay the defaults so the UI can retune planner economics, control safety limits and the theme
WITHOUT a redeploy. Effective config = defaults + valid runtime settings.

Everything here is pure (no I/O) so it is trivially unit-testable. `storage/settings.py` handles
persistence and `web/api.py` keeps an in-memory cache it refreshes on write.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SettingsField:
    """One editable setting. `type` drives both validation and how the UI renders the control."""

    key: str
    label: str
    type: str  # "number" | "int" | "bool" | "enum"
    default: Any
    group: str
    help: str = ""
    min: float | None = None
    max: float | None = None
    options: tuple[str, ...] | None = None
    step: float | None = None
    unit: str = ""


# The editable surface. Keep keys stable — they are persisted and consumed by the UI.
SETTINGS_SCHEMA: tuple[SettingsField, ...] = (
    # Planner economics — change these and /api/plan recomputes immediately (SPEC §8.3).
    SettingsField(
        "planner.round_trip_efficiency", "Round-trip efficiency", "number", 0.90, "planner",
        help="Battery charge→discharge efficiency. Lower means fewer trades clear break-even.",
        min=0.5, max=1.0, step=0.01,
    ),
    SettingsField(
        "planner.degradation_eur_per_kwh", "Degradation cost", "number", 0.05, "planner",
        help="Wear cost charged against every stored kWh.",
        min=0.0, max=0.5, step=0.01, unit="€/kWh",
    ),
    SettingsField(
        "planner.risk_margin_eur_per_kwh", "Risk margin", "number", 0.02, "planner",
        help="Extra spread required before a trade is judged worthwhile.",
        min=0.0, max=0.5, step=0.01, unit="€/kWh",
    ),
    SettingsField(
        "planner.charge_slots", "Charge window", "int", 12, "planner",
        help="How many of the cheapest 15-min slots to charge in (12 ≈ 3h).",
        min=1, max=96, unit="slots",
    ),
    SettingsField(
        "planner.discharge_slots", "Discharge window", "int", 24, "planner",
        help="Maximum number of expensive slots to discharge into (24 ≈ 6h).",
        min=1, max=96, unit="slots",
    ),
    # Control safety limits — pushed onto the mode controller live (SPEC §6.5).
    SettingsField(
        "control.max_switches_per_day", "Max mode switches/day", "int", 10, "control",
        help="Hard cap on battery mode writes per day (SPEC target: under 10). Ceiling 20.",
        min=1, max=20,
    ),
    SettingsField(
        "control.min_dwell_seconds", "Min dwell", "number", 600.0, "control",
        help="Minimum seconds to hold a mode before another switch is allowed (floor 60s).",
        min=60.0, max=3600.0, unit="s",
    ),
    SettingsField(
        "control.allow_export_discharge", "Allow export discharge", "bool", False, "control",
        help="Permit forced DISCHARGE for export. Off = serve load via vendor AUTO (fail-safe).",
    ),
    # Battery & reserve — feed the "tonight's charge target" readout (SPEC §8.3 step 1).
    SettingsField(
        "battery.usable_kwh", "Usable capacity", "number", 10.8, "battery",
        help="Usable energy of the battery cluster (SolidFlex 2000 ×2 ≈ 10.8 kWh).",
        min=1.0, max=50.0, step=0.1, unit="kWh",
    ),
    SettingsField(
        "battery.min_reserve_soc", "Reserve floor", "number", 10.0, "battery",
        help="State of charge the EMS never discharges below.",
        min=0.0, max=50.0, step=1.0, unit="%",
    ),
    SettingsField(
        "battery.night_reserve_kwh", "Night reserve", "number", 2.0, "battery",
        help="Extra buffer to hold for the night, on top of the expected load.",
        min=0.0, max=20.0, step=0.5, unit="kWh",
    ),
    SettingsField(
        "battery.overnight_load_kwh", "Overnight load", "number", 6.0, "battery",
        help="Estimated house consumption from sunset to sunrise.",
        min=0.0, max=50.0, step=0.5, unit="kWh",
    ),
    # Solar array — change these and /api/forecast recomputes immediately (mock derate; real
    # adapters configure orientation on their own side).
    SettingsField(
        "site.kwp", "Array size", "number", 3.0, "site",
        help="Installed PV peak power.", min=0.5, max=30.0, step=0.1, unit="kWp",
    ),
    SettingsField(
        "site.tilt", "Panel tilt", "number", 35.0, "site",
        help="Tilt from horizontal (0 = flat, 90 = vertical). Optimal ≈ 35°.",
        min=0.0, max=90.0, step=1.0, unit="°",
    ),
    SettingsField(
        "site.azimuth", "Panel azimuth", "number", 0.0, "site",
        help="Compass orientation: 0 = due south, −90 = east, +90 = west.",
        min=-180.0, max=180.0, step=5.0, unit="°",
    ),
    # Presentation.
    SettingsField(
        "ui.theme", "Theme", "enum", "auto", "ui",
        help="Dashboard colour theme.", options=("auto", "dark", "light"),
    ),
)

SETTINGS_BY_KEY: dict[str, SettingsField] = {f.key: f for f in SETTINGS_SCHEMA}


def defaults() -> dict[str, Any]:
    """The default value of every setting (the config.yaml-equivalent baseline)."""
    return {f.key: f.default for f in SETTINGS_SCHEMA}


def schema_json() -> list[dict]:
    """Serialize the schema for the UI to render a form generically."""
    return [
        {
            "key": f.key, "label": f.label, "type": f.type, "default": f.default,
            "group": f.group, "help": f.help, "min": f.min, "max": f.max,
            "options": list(f.options) if f.options else None, "step": f.step, "unit": f.unit,
        }
        for f in SETTINGS_SCHEMA
    ]


def _coerce(field: SettingsField, value: Any) -> tuple[bool, Any]:
    """Validate+coerce one value against its field. Returns (ok, coerced_value | error_message)."""
    if field.type == "bool":
        if not isinstance(value, bool):
            return False, "must be true or false"
        return True, value
    if field.type == "enum":
        opts = field.options or ()
        if value not in opts:
            return False, f"must be one of: {', '.join(opts)}"
        return True, value
    if field.type in ("number", "int"):
        # bool is a subclass of int in Python — reject it explicitly so True != 1 here.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, "must be a number"
        num: float = float(value)
        if field.type == "int":
            if num != int(num):
                return False, "must be a whole number"
            num = int(num)
        if field.min is not None and num < field.min:
            return False, f"must be >= {field.min}"
        if field.max is not None and num > field.max:
            return False, f"must be <= {field.max}"
        return True, num
    return False, "unsupported setting type"  # pragma: no cover - guards a bad schema entry


def validate_settings(partial: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate a partial settings dict. Returns (clean, errors): only valid keys land in `clean`,
    every rejected key gets a human-readable message in `errors`. Unknown keys are errors."""
    if not isinstance(partial, dict):
        return {}, {"_": "expected a JSON object of setting keys to values"}
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, value in partial.items():
        field = SETTINGS_BY_KEY.get(key)
        if field is None:
            errors[key] = "unknown setting"
            continue
        ok, result = _coerce(field, value)
        if ok:
            clean[key] = result
        else:
            errors[key] = result
    return clean, errors


def effective_settings(stored: Any) -> dict[str, Any]:
    """Defaults overlaid by the valid subset of `stored`. Invalid/unknown stored values are
    silently dropped on read (tolerant) — a bad persisted row must never break the dashboard."""
    eff = defaults()
    clean, _errors = validate_settings(stored if isinstance(stored, dict) else {})
    eff.update(clean)
    return eff
