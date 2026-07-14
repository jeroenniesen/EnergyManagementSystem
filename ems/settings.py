"""UI-editable runtime settings (SPEC §9.4): a schema + validation + effective-config overlay.

`config.yaml` holds read-only **defaults**; these settings live in the runtime store (/data) and
overlay the defaults so the UI can retune the planner, control limits, the connected devices and
the theme WITHOUT editing files. Effective config = defaults + valid runtime settings.

Fields are tagged `advanced` (hidden behind the UI's Advanced toggle) and `applies`:
"live" takes effect on save; "restart" (device/service connection) is read at startup.

Everything here is pure (no I/O) so it is trivially unit-testable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ems.ev_schedule import default_schedule


@dataclass(frozen=True)
class SettingsField:
    """One editable setting. `type` drives both validation and how the UI renders the control."""

    key: str
    label: str
    type: str  # number | int | bool | enum | text | secret
    default: Any
    group: str
    help: str = ""
    min: float | None = None
    max: float | None = None
    options: tuple[str, ...] | None = None
    step: float | None = None
    unit: str = ""
    advanced: bool = False  # hidden behind the "Advanced" toggle in the UI
    applies: str = "live"  # "live" = on save · "restart" = connection, read at startup
    slider: bool = False  # render as a drag slider (needs min+max) instead of a number box


# The editable surface. Keep keys stable — they are persisted and consumed by the UI.
SETTINGS_SCHEMA: tuple[SettingsField, ...] = (
    # --- Strategy: how the battery is run (the headline choice) ---
    SettingsField(
        "strategy.mode", "Strategy", "enum", "auto", "strategy",
        help="Auto follows the season. Summer fills from your panels and runs the night on the "
        "battery. Winter charges cheap and discharges the expensive peaks.",
        options=("auto", "summer", "winter"),
    ),
    SettingsField(
        "strategy.summer_grid_topup", "Summer: top up from the grid", "bool", True, "strategy",
        help="If the sun won't fill the battery for the night, buy the shortfall in the cheapest "
        "hours. Off = use only solar (the battery may not last the whole night).",
    ),
    SettingsField(
        "strategy.summer_max_topup_price", "Summer: max top-up price", "number", 0.30, "strategy",
        help="Never grid-charge in summer above this price.",
        min=0.0, max=2.0, step=0.01, unit="€/kWh", advanced=True,
    ),
    SettingsField(
        "strategy.hysteresis_days", "Season switch hysteresis", "int", 3, "strategy",
        help="On Auto, how many consecutive days the solar/price signal must lean the other way "
        "before the season actually switches. Stops shoulder-month days (March, October) from "
        "flip-flopping solar-first↔price-smart. 0 = switch the instant the signal flips.",
        min=0, max=14, unit="days", advanced=True,
    ),
    # --- Connection: which sources to use (device/service wiring; applied at startup) ---
    SettingsField(
        "connection.use_live_devices", "Use live devices", "bool", False, "connection",
        help="Read the real HomeWizard meters + Indevolt battery below. Off = built-in simulator.",
        applies="restart",
    ),
    SettingsField(
        "connection.use_live_prices", "Use live Tibber prices", "bool", False, "connection",
        help="Fetch real day-ahead prices from Tibber. Off = a simulated day/night price curve.",
        applies="restart",
    ),
    # --- Energy meters (HomeWizard local API) ---
    SettingsField(
        "meters.p1_ip", "Grid meter IP (P1)", "text", "", "meters",
        help="HomeWizard meter on your home's grid connection — measures power bought and sold.",
        applies="restart",
    ),
    SettingsField(
        "meters.solar_ip", "Solar meter IP", "text", "", "meters",
        help="HomeWizard kWh meter measuring PV production.", applies="restart",
    ),
    SettingsField(
        "meters.car_ip", "EV meter IP", "text", "", "meters",
        help="HomeWizard kWh meter measuring EV charging.", applies="restart",
    ),
    # --- Electricity prices (Tibber) ---
    SettingsField(
        "prices.tibber_token", "Tibber access token", "secret", "", "prices",
        help="Personal token from developer.tibber.com. Stored locally; leave blank to keep the "
        "current value.", applies="restart",
    ),
    SettingsField(
        "prices.export_price_model", "Export (feed-in) value", "enum", "net_metering", "prices",
        help="How much each kWh you export (feed back to the grid) is worth. Until 2027, Dutch "
        "net-metering (saldering) nets your export against your import at the FULL price — that's "
        "net-metering, today's behaviour. Switch to spot-minus-tax when saldering ends (2027), or "
        "if your dynamic contract already pays the spot price minus energy tax for export; pick "
        "fixed if your contract pays a flat feed-in tariff regardless of the spot price.",
        options=("net_metering", "spot_minus_tax", "fixed"),
    ),
    SettingsField(
        "prices.energy_tax_eur_per_kwh", "Export energy tax", "number", 0.13, "prices",
        help="Energy tax subtracted from the spot price when export is valued at spot-minus-tax "
        "(post-2027 dynamic contracts).",
        min=0.0, max=0.5, step=0.005, unit="€/kWh", advanced=True,
    ),
    SettingsField(
        "prices.fixed_feed_in_eur_per_kwh", "Fixed feed-in tariff", "number", 0.01, "prices",
        help="Flat price paid per exported kWh when the export value is set to fixed.",
        min=0.0, max=0.5, step=0.005, unit="€/kWh", advanced=True,
    ),
    # --- Battery (Indevolt) — connection + capacity/reserve ---
    SettingsField(
        "battery.indevolt_ip", "Indevolt main tower IP", "text", "", "battery",
        help="The master tower. Used for control writes; the cluster is commanded as one device.",
        applies="restart",
    ),
    SettingsField(
        "battery.indevolt_ips_extra", "Additional tower IPs", "text", "", "battery",
        help="Other Indevolt towers (comma-separated). Each is read; the dashboard shows the "
        "capacity-weighted average SoC of the whole cluster.", applies="restart",
    ),
    SettingsField(
        "battery.indevolt_port", "Indevolt port", "int", 8080, "battery",
        help="Local API port (same for every tower).", min=1, max=65535, advanced=True,
        applies="restart",
    ),
    SettingsField(
        "battery.usable_kwh", "Usable capacity", "number", 10.8, "battery",
        help="Usable energy of the battery cluster (SolidFlex 2000 ×2 ≈ 10.8 kWh).",
        min=1.0, max=50.0, step=0.1, unit="kWh",
    ),
    SettingsField(
        "battery.min_reserve_soc", "Minimum reserve", "number", 10.0, "battery",
        help="The system always keeps at least this much charge in the battery and never goes "
        "below it — your safety buffer.",
        min=0.0, max=50.0, step=1.0, unit="%",
    ),
    SettingsField(
        "battery.max_charge_w", "Max charge power", "number", 4000.0, "battery",
        help="Peak charge power of the cluster — used to project how fast it fills.",
        min=200.0, max=20000.0, step=100.0, unit="W", advanced=True,
    ),
    SettingsField(
        "battery.max_discharge_w", "Max discharge power", "number", 4000.0, "battery",
        help="Peak discharge power of the cluster — used to project how fast it drains.",
        min=200.0, max=20000.0, step=100.0, unit="W", advanced=True,
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
    # --- Solar array & location (drive the solar forecast) ---
    SettingsField(
        "site.kwp", "Array size", "number", 3.0, "site",
        help="Installed PV peak power.", min=0.5, max=30.0, step=0.1, unit="kWp",
    ),
    SettingsField(
        "site.lat", "Latitude", "number", 52.13, "site",
        help="Site latitude (decimal degrees) — used for the solar forecast.",
        min=-90.0, max=90.0, step=0.01, unit="°",
    ),
    SettingsField(
        "site.lon", "Longitude", "number", 5.29, "site",
        help="Site longitude (decimal degrees) — used for the solar forecast.",
        min=-180.0, max=180.0, step=0.01, unit="°",
    ),
    SettingsField(
        "site.tilt", "Panel tilt", "number", 35.0, "site",
        help="Tilt from horizontal (0 = flat, 90 = vertical). Optimal ≈ 35°.",
        min=0.0, max=90.0, step=1.0, unit="°", advanced=True,
    ),
    SettingsField(
        "site.azimuth", "Panel azimuth", "number", 0.0, "site",
        help="Compass orientation: 0 = due south, −90 = east, +90 = west.",
        min=-180.0, max=180.0, step=5.0, unit="°", advanced=True,
    ),
    # --- Control safety limits (pushed onto the mode controller live, SPEC §6.5) ---
    SettingsField(
        "control.operational", "Let the system control the battery", "bool", False, "control",
        help="OFF (safe default): the system only shows what it would do — your battery is never "
        "changed. ON: the system actually switches the battery's mode, within all the safety "
        "limits below. Takes effect after a restart.",
        applies="restart",
    ),
    SettingsField(
        "control.max_switches_per_day", "Max mode switches/day", "int", 10, "control",
        help="Hard cap on battery mode writes per day (SPEC target: under 10). Ceiling 20.",
        min=1, max=20,
    ),
    SettingsField(
        "control.min_dwell_seconds", "Min dwell", "number", 600.0, "control",
        help="Minimum seconds to hold a mode before another switch is allowed (floor 60s).",
        min=60.0, max=3600.0, unit="s", advanced=True,
    ),
    SettingsField(
        "control.allow_export_discharge", "Allow export discharge", "bool", False, "control",
        help="Permit forced DISCHARGE for export. Off = serve load via vendor AUTO (fail-safe).",
        advanced=True,
    ),
    SettingsField(
        "control.hold_battery_when_car_charging", "Hold battery while the car charges", "bool",
        True, "control",
        help="When the car is charging, put the battery in standby (real-time idle) so it neither "
        "discharges into the car nor charges — the car is served by solar + grid. Needs the EV "
        "meter configured to detect charging. Re-checked every control cycle.",
    ),
    SettingsField(
        "control.car_charging_threshold_w", "Car-charging threshold", "number", 500.0, "control",
        help="EV power above this counts as 'charging' for the hold-battery rule.",
        min=100.0, max=11000.0, step=100.0, unit="W", advanced=True,
    ),
    SettingsField(
        "control.live_read_seconds", "Battery/meter read interval", "number", 60.0, "control",
        help="How often the app actually reads the battery + meters (live values are reused in "
        "between). RAISE this if the battery/Indevolt app feels slow or the master drops offline — "
        "the battery's little server is shared with Home Assistant + the Indevolt app, so reading "
        "less often eases the load. Lower = more up-to-the-second dashboard.",
        min=15.0, max=300.0, step=15.0, unit="s", slider=True,
    ),
    # --- Planner economics (advanced — change these and /api/plan recomputes, SPEC §8.3) ---
    SettingsField(
        "planner.solar_confidence", "Solar forecast confidence", "number", 80.0, "planner",
        help="How much of the expected solar forecast to count on when deciding the grid top-up. "
        "Higher = trust the forecast and buy less grid power; lower = more cautious (buys more to "
        "guarantee the overnight charge). 100% counts on the full forecast; 60% ≈ the old cautious "
        "default. Raise it if you see grid charging on days the sun clearly covers the battery.",
        min=30.0, max=100.0, step=5.0, unit="%", slider=True,
    ),
    SettingsField(
        "planner.round_trip_efficiency", "Round-trip efficiency", "number", 0.90, "planner",
        help="How much energy survives a charge-then-discharge cycle (0.90 = 90%). Lower means the "
        "system only acts when the savings clearly beat the losses.",
        min=0.5, max=1.0, step=0.01, advanced=True,
    ),
    SettingsField(
        "planner.degradation_eur_per_kwh", "Battery wear cost", "number", 0.05, "planner",
        help="Wear cost per kWh the battery DISCHARGES (delivers) — this prices a full "
        "charge→discharge cycle once, so the system won't cycle the battery for tiny gains. "
        "Used both in the planner's break-even and in the measured savings.",
        min=0.0, max=0.5, step=0.01, unit="€/kWh discharged", advanced=True,
    ),
    SettingsField(
        "planner.risk_margin_eur_per_kwh", "Safety margin", "number", 0.02, "planner",
        help="Extra price gap the system needs before it bothers to trade — a buffer against "
        "forecast error.",
        min=0.0, max=0.5, step=0.01, unit="€/kWh", advanced=True,
    ),
    SettingsField(
        "planner.charge_slots", "Charge window", "int", 12, "planner",
        help="How long to charge in the cheapest part of the day. Each step is 15 minutes, so "
        "12 ≈ 3 hours.",
        min=1, max=96, unit="× 15 min", advanced=True,
    ),
    SettingsField(
        "planner.discharge_slots", "Discharge window", "int", 24, "planner",
        help="Most the battery will cover during expensive hours. Each step is 15 minutes, so "
        "24 ≈ 6 hours.",
        min=1, max=96, unit="× 15 min", advanced=True,
    ),
    SettingsField(
        "planner.negative_price_soak", "Charge on negative prices", "bool", False, "planner",
        help="When the electricity price goes below zero you are PAID to consume. With this on, "
        "the planner adds negative-price slots as battery-charge slots (up to battery headroom), "
        "even outside normal charge windows and even when summer grid top-up is off. Off = today's "
        "behaviour.",
    ),
    SettingsField(
        "planner.validate_projection", "Keep plans honest about reachability", "bool", True,
        "planner",
        help="Before acting, the system checks whether the plan can really reach its target "
             "in time. "
        "If not, it keeps your battery in its safe automatic mode. On by default, and skipped "
        "when the forecast is too stale to trust.",
        advanced=True,
    ),
    SettingsField(
        "planner.recovery_enabled", "Recover a missed charge window", "bool", True, "planner",
        help="If a planned low-cost charge is missed and the deadline is still ahead, the system "
        "can use the best worthwhile slots that remain. It never bypasses the same safety checks "
        "or buys above its break-even limit. Turn it off to leave missed windows untouched.",
        advanced=True,
    ),
    # --- AI explanations (optional, OFF by default; the one off-device feature — SPEC §12) ---
    SettingsField(
        "explainer.mode", "AI explanations", "enum", "template", "ai",
        help="Off (default) shows the built-in plain-language reasons. On sends a tiny, "
        "non-identifying summary (the decision + the few numbers it cites — never your address, "
        "history or tokens) to a cloud AI to phrase it more naturally and to power the chat. "
        "Falls back to the built-in text if the AI is slow or unavailable.",
        options=("template", "external_llm"),
    ),
    SettingsField(
        "explainer.language", "Explanation language", "enum", "English", "ai",
        help="Language for AI explanations and chat answers.",
        options=("English", "Dutch"),
    ),
    SettingsField(
        "explainer.api_key", "MiniMax API key", "secret", "", "ai",
        help="Your MiniMax API key (from platform.minimax.io). Stored locally as a secret, never "
        "logged. Leave blank to keep the current value.",
    ),
    SettingsField(
        "explainer.model", "AI model", "text", "MiniMax-M2.7", "ai",
        help="The chat model id. MiniMax-M2.7 is a cheap, capable default included in current "
        "MiniMax tiers (M2.5 is legacy and may not be on your plan).", advanced=True,
    ),
    SettingsField(
        "explainer.base_url", "AI endpoint", "text", "https://api.minimax.io/v1", "ai",
        help="OpenAI-compatible chat endpoint. Point this at any compatible provider (or a "
        "zero-retention gateway) — the app isn't locked to one vendor.", advanced=True,
    ),
    SettingsField(
        "explainer.max_tokens", "AI reply length", "int", 1024, "ai",
        help="Token budget per AI reply. Reasoning models (e.g. MiniMax-M2.7) spend tokens "
        "thinking before they answer, so keep this generous or replies get cut off mid-thought.",
        min=20, max=4000, advanced=True,
    ),
    SettingsField(
        "explainer.timeout_seconds", "AI timeout", "number", 30.0, "ai",
        help="Give up on the AI after this many seconds and use the built-in text instead. "
        "Reasoning models think before answering, so an open-ended chat can take 10-20s.",
        min=1.0, max=60.0, step=1.0, unit="s", advanced=True,
    ),
    SettingsField(
        "explainer.validate_hours", "AI second-opinion interval", "number", 24.0, "ai",
        help="How often the AI reviews the current plan as an independent advisory check (logged "
        "in the Audit tab). 0 turns it off. Only runs when AI is on; never changes anything.",
        min=0.0, max=168.0, step=1.0, unit="h",
    ),
    SettingsField(
        "explainer.cache_hours", "AI explanation cache", "number", 168.0, "ai",
        help="Reuse a generated explanation for an identical decision for this long (it survives "
        "restarts), so the same decision isn't re-sent to the AI and tokens aren't re-spent. "
        "0 disables the persistent cache.", min=0.0, max=720.0, step=1.0, unit="h", advanced=True,
    ),
    # --- Access & security ---
    SettingsField(
        "web.auth_token", "Web access token", "secret", "", "access",
        help="Optional. Set a token to require it (as a Bearer token) for any change — saving "
        "settings, manual override, control. Leave blank for open access on your home LAN. "
        "Once set, enter the same token in the Access box at the top to authorise this browser.",
    ),
    SettingsField(
        "web.require_auth", "Require the token to view too", "bool", False, "access",
        help="Off (default): the read-only dashboard is open on your LAN; only changes need the "
        "token. On: EVERY page and API read also requires the token. Turn this ON before reaching "
        "the app over a VPN or from outside your home — otherwise anyone who reaches the port can "
        "read your energy data. Requires a token to be set.",
    ),
    # --- Insights & reporting (CO₂ accounting factors) ---
    SettingsField(
        "reporting.carbon_signal", "Grid CO₂ signal", "enum", "static", "reporting",
        help="Static (default) uses the flat grid CO₂ factor below — works offline, no key. Live "
        "(electricityMaps) fetches the grid's actual CO₂ intensity as it varies through the day — "
        "needs a free personal API key from electricitymaps.com. Falls back to the flat factor if "
        "the key is missing or the live signal is ever unavailable. Reporting only: this never "
        "changes when or how the battery is controlled. Takes effect after a restart.",
        options=("static", "electricitymaps"), applies="restart",
    ),
    SettingsField(
        "reporting.electricitymaps_api_key", "electricityMaps API key", "secret", "", "reporting",
        help="Personal API key from electricitymaps.com (free tier). Stored locally; leave blank "
        "to keep the current value. Only used when the grid CO₂ signal above is set to live.",
        applies="restart",
    ),
    SettingsField(
        "reporting.grid_co2_factor", "Grid CO₂ factor", "number", 0.27, "reporting",
        help="kg CO₂ per kWh of imported electricity, used by the CO₂ score. NL grid-mix ≈ 0.27 "
        "(2025, trending down). Lower it as the grid greens.",
        min=0.0, max=1.0, step=0.01, unit="kg/kWh", advanced=True,
    ),
    SettingsField(
        "reporting.gas_co2_factor", "Gas CO₂ factor", "number", 1.78, "reporting",
        help="kg CO₂ per m³ of natural gas burned (combustion ≈ 1.78). Used once gas metering is "
        "added to the CO₂ score.",
        min=0.0, max=5.0, step=0.01, unit="kg/m³", advanced=True,
    ),
    SettingsField(
        "reporting.gas_price_eur_per_m3", "Gas price", "number", 1.40, "reporting",
        help="Your gas contract's variable price per m³ (incl. tax) — used for the Insights gas "
        "panel.",
        min=0.0, max=5.0, step=0.05, unit="€/m³",
    ),
    # App state, not a tunable: which of the Insights heating-advice cards (balancing/flow_temp/
    # dhw_eco) the household has marked done, and when — a JSON object of {item key: "YYYY-MM-DD"}.
    # Written directly by HeatingAdvice.tsx's "Mark as done"/"Undo" (POSTs only this one key,
    # immediately, never through the Settings save bar). Generic "text" validation only — the shape
    # is owned by the UI, same as ev.schedule above. No `hidden` flag exists in SettingsField, so
    # this still renders in the two-pane Settings UI; harmless, and the help text says why.
    SettingsField(
        "heating.done", "Heating advice: done items", "text", "{}", "reporting",
        help="Managed from the Insights heating cards — not meant to be edited here.",
    ),
    # --- Notifications (optional, off by default — B-20 outbox + ntfy push channel) ---
    SettingsField(
        "notify.ntfy_url", "ntfy server", "text", "", "notify",
        help="Base URL of an ntfy server — the free, shared https://ntfy.sh, or your own "
        "self-hosted instance. Blank (default) = in-app notifications only, no phone push. "
        "Install the free ntfy app (iOS/Android) and subscribe to the topic below to get real "
        "push notifications with no Apple/Google account and no cloud subscription.",
    ),
    SettingsField(
        "notify.ntfy_topic", "ntfy topic", "text", "", "notify",
        help="A private, hard-to-guess topic name — anyone who knows it can read what's "
        "published there, so avoid anything guessable (a random string is safest). Subscribe to "
        "the exact same topic in the ntfy app.",
    ),
    # --- Car (v2 EV control is out of scope — advisory only, docs/v2-ev-control.md) ---
    SettingsField(
        "ev.advice_enabled", "Show best-time-to-charge card", "bool", False, "ev",
        help="Off by default so non-EV homes never see it. On shows a dashboard card suggesting "
        "the cheapest window to plug in the car before it needs to leave. Advisory only — the "
        "EMS never controls the car.",
    ),
    SettingsField(
        "ev.car_id", "Car", "text", "", "ev",
        help="Pick your car so capacity and AC limit are right.",
    ),
    SettingsField(
        "ev.battery_kwh", "Battery capacity", "number", 57.5, "ev",
        help="Usable battery capacity — autofilled from the car picker, override if you know "
        "better.",
        min=10.0, max=150.0, step=0.5, unit="kWh",
    ),
    SettingsField(
        "ev.charge_efficiency", "Charging efficiency", "number", 0.90, "ev",
        help="AC energy → battery energy factor (charging losses).",
        min=0.7, max=1.0, step=0.01, advanced=True,
    ),
    SettingsField(
        "ev.departure_time", "Usual departure time", "text", "07:30", "ev",
        help="When the car usually needs to be ready (24h HH:MM).",
    ),
    SettingsField(
        "ev.charge_kwh", "Typical energy to add", "number", 20.0, "ev",
        help="Roughly how much energy a typical top-up adds.",
        min=1.0, max=100.0, step=1.0, unit="kWh",
    ),
    SettingsField(
        "ev.charger_kw", "Charger power", "number", 11.0, "ev",
        help="The car charger's power — sets how long a charge takes.",
        min=1.0, max=22.0, step=0.5, unit="kW",
    ),
    SettingsField(
        "ev.schedule", "Weekly charge schedule", "text", json.dumps(default_schedule()), "ev",
        help="Weekly minimum charge schedule — edited with the schedule editor below.",
    ),
    # --- Appearance ---
    SettingsField(
        "ui.theme", "Theme", "enum", "auto", "ui",
        help="Dashboard colour theme.", options=("auto", "dark", "light"),
    ),
)

SETTINGS_BY_KEY: dict[str, SettingsField] = {f.key: f for f in SETTINGS_SCHEMA}
SECRET_KEYS: frozenset[str] = frozenset(f.key for f in SETTINGS_SCHEMA if f.type == "secret")


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
            "advanced": f.advanced, "applies": f.applies, "slider": f.slider,
        }
        for f in SETTINGS_SCHEMA
    ]


def _coerce(field: SettingsField, value: Any) -> tuple[bool, Any]:
    """Validate+coerce one value against its field. Returns (ok, coerced_value | error_message)."""
    if field.type == "bool":
        if not isinstance(value, bool):
            return False, "must be true or false"
        return True, value
    if field.type in ("text", "secret"):
        if not isinstance(value, str):
            return False, "must be text"
        return True, value.strip()
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
    every rejected key gets a human-readable message in `errors`. Unknown keys are errors.

    A secret submitted as "" means "leave unchanged" — it is dropped from `clean` (not stored)."""
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
        if not ok:
            errors[key] = result
        elif field.type == "secret" and result == "":
            continue  # blank secret = keep the existing value
        else:
            clean[key] = result
    return clean, errors


def effective_settings(stored: Any) -> dict[str, Any]:
    """Defaults overlaid by the valid subset of `stored`. Invalid/unknown stored values are
    silently dropped on read (tolerant) — a bad persisted row must never break the dashboard."""
    eff = defaults()
    clean, _errors = validate_settings(stored if isinstance(stored, dict) else {})
    eff.update(clean)
    return eff


def public_values(values: dict[str, Any]) -> dict[str, Any]:
    """Effective values with secrets masked (never leak a token over the API). A masked secret is
    reported as "" plus a parallel `<key>.__set` boolean is added so the UI can show 'set'."""
    out = dict(values)
    for key in SECRET_KEYS:
        was_set = bool(out.get(key))
        out[key] = ""
        out[f"{key}.__set"] = was_set
    return out
