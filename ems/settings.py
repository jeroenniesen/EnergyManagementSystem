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

from ems.consumers.selection import default_device_ids_json
from ems.ev_schedule import default_schedule
from ems.ha_url import normalize_ha_url


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
    # Show this field only when every (other_key, required_value) pair matches the current
    # (edited) settings values. Empty = always visible. Used e.g. to gate Solcast credentials
    # behind solar.forecast_provider=solcast.
    visible_when: tuple[tuple[str, str], ...] = ()
    # Enum tokens that appear in the UI (greyed) but cannot be selected or saved. Used e.g. to
    # preview future planner.mode values (ml / advisory) until M6 ships adapters.
    disabled_options: tuple[str, ...] = ()
    # When False, the key stays valid and persisted but is omitted from schema_json() so the
    # Settings form does not render a second editor. Trading owns live export arming.
    show_in_settings: bool = True


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
        "planner.max_daily_grid_charge_kwh", "Max grid charge per day", "number", 12.0,
        "strategy",
        help="Hard cap on energy the battery may buy from the grid on one local day. A bad "
        "forecast or setting can never buy more than this. Charging while the price is negative "
        "counts toward the same cap. The counter resets at local midnight.",
        min=1.0, max=40.0, step=0.5, unit="kWh", slider=True,
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
        "prices.backup_fixed_eur_per_kwh", "Backup fixed import price", "number", 0.0, "prices",
        help="When Tibber is unreachable and no last-good cache exists, use this flat €/kWh for "
        "display/reporting (#118). 0 = disabled (empty prices). Never used to drive live "
        "grid-charge — EMS stays in self-use while the backup is active.",
        min=0.0, max=2.0, step=0.005, unit="€/kWh", advanced=True, applies="restart",
    ),
    SettingsField(
        "prices.export_price_model", "Export (feed-in) value", "enum", "spot_minus_tax", "prices",
        help="How much each kWh you export (feed back to the grid) is worth. Default is "
        "spot-minus-tax (2027-ready: spot price minus energy tax — may be low or negative). "
        "Switch to net-metering only if you still want today's Dutch saldering valuation "
        "(export nets against import at the FULL price). Pick fixed if your contract pays a "
        "flat feed-in tariff regardless of the spot price.",
        options=("spot_minus_tax", "net_metering", "fixed"),
    ),
    SettingsField(
        "prices.export_model_confirmed", "Export model confirmed", "bool", False, "prices",
        help="Set when the household explicitly chose saldering vs the 2027-ready export model "
        "(#341 / audit F-11). Omitted from Manage — the Home/Settings banner owns the choice.",
        show_in_settings=False,
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
    SettingsField(
        "grid_fees.price_includes_all_fees", "Price includes all grid fees", "bool", False,
        "prices",
        help="Enable when the price provider's total already includes every import fee "
        "(Tibber `total` usually does; a fixed tariff often does not). When off, the configured "
        "import fee is added for planning and reporting. Legacy key "
        "`grid_fees.tibber_total_includes_all` is accepted for one release (#119).",
        advanced=True,
    ),
    SettingsField(
        "grid_fees.import_fee_eur_per_kwh", "Import grid fee", "number", 0.0, "prices",
        help="Additional cost per imported kWh when the provider total excludes grid fees.",
        min=0.0, max=1.0, step=0.005, unit="€/kWh", advanced=True,
    ),
    SettingsField(
        "grid_fees.export_fee_eur_per_kwh", "Export grid fee", "number", 0.0, "prices",
        help="Fee deducted from the value of each exported kWh after saldering ends.",
        min=0.0, max=1.0, step=0.005, unit="€/kWh", advanced=True,
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
        help="Usable energy. A single SolidFlex 2000 defaults to ≈5.4 kWh; multiple towers are "
        "summed.",
        min=1.0, max=50.0, step=0.1, unit="kWh",
    ),
    SettingsField(
        "battery.min_reserve_soc", "Minimum reserve", "number", 10.0, "battery",
        help="The system always keeps at least this much charge in the battery and never goes "
        "below it — your safety buffer. Floor is 5% (Indevolt minimum charge target); values "
        "below that are rejected so plans are not marked unsafe for targets the battery would "
        "floor-and-run (#245).",
        # #245: align with CapabilityReport.min_target_soc / indevolt_driver._MIN_SOC (5%).
        min=5.0, max=50.0, step=1.0, unit="%",
    ),
    SettingsField(
        "battery.max_charge_w", "Max charge power", "number", 4000.0, "battery",
        help="Peak charge power. Defaults to 2.4 kW per tower and scales for multiple towers.",
        min=200.0, max=20000.0, step=100.0, unit="W", advanced=True,
    ),
    SettingsField(
        "battery.max_discharge_w", "Max discharge power", "number", 4000.0, "battery",
        help="Peak discharge power. Defaults to 2.4 kW per tower and scales for multiple towers.",
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
    # --- Solar forecast provider (SPEC §6.3 / B-14) ---
    # Default Forecast.Solar until the operator explicitly selects Solcast.
    SettingsField(
        "solar.forecast_provider", "Solar forecast provider", "enum", "forecast_solar", "site",
        help="Forecast.Solar is keyless (default). Choose Solcast Hobbyist for real P10/P50/P90 "
        "percentiles — then enter the API key and rooftop resource id below. Solcast falls back "
        "to Forecast.Solar automatically when stale, unreachable, or over budget. Options come "
        "from the forecast adapter registry (#113). `mock` is the built-in synthetic daylight "
        "curve (demo/mock run mode only; also used automatically when live devices are off).",
        # Baseline list — schema_json / validation overlay live registry names via
        # `_registry_enum_options` so a new `@register_forecast_provider` appears without a
        # second hard-coded enum here.
        options=("forecast_solar", "solcast", "mock"), applies="restart",
    ),
    SettingsField(
        "solar.solcast_api_key", "Solcast API key", "secret", "", "site",
        help="Hobbyist API key from toolkit.solcast.com.au (Account menu). Stored locally; leave "
        "blank to keep the current value. Or set env SOLCAST_API_KEY before first boot.",
        applies="restart",
        visible_when=(("solar.forecast_provider", "solcast"),),
    ),
    SettingsField(
        "solar.solcast_resource_id", "Solcast rooftop resource id", "text", "", "site",
        help="rooftop_resource_id from the Solcast Toolkit site detail page. Or set env "
        "SOLCAST_RESOURCE_ID before first boot.",
        applies="restart",
        visible_when=(("solar.forecast_provider", "solcast"),),
    ),
    SettingsField(
        "solar.solcast_daily_call_budget", "Solcast daily call budget", "int", 10, "site",
        help="Hard cap on Solcast API calls per local day (free Hobbyist = 10). The EMS refuses "
        "further refreshes once the ledger hits this cap and falls back to Forecast.Solar.",
        min=1, max=50, advanced=True, applies="restart",
        visible_when=(("solar.forecast_provider", "solcast"),),
    ),
    # --- Control safety limits (pushed onto the mode controller live, SPEC §6.5) ---
    # Runtime watch-only toggle (#171). Default True = fail-safe. Seeded from config.yaml on
    # first boot so a production host with yaml dry_run:false keeps live arming after upgrade.
    # config.yaml dry_run / mock|replay still always wins (#136) — see resolve_force_dry_run.
    SettingsField(
        "control.dry_run", "Watch only (no battery writes)", "bool", True, "control",
        help="ON (safe default): log decisions only — the battery is never written. OFF: allow "
        "live control when 'Let the system control the battery' is also ON, devices/prices are "
        "live, and config.yaml is not forcing watch-only (dev.mode: live and control.dry_run: "
        "false). Takes effect after Apply & restart — a restart while charging can hand the "
        "battery back to AUTO and abort an active cheap-charge window.",
        applies="restart",
    ),
    SettingsField(
        "control.operational", "Let the system control the battery", "bool", False, "control",
        help="OFF (safe default): the system only shows what it would do — your battery is never "
        "changed. ON: the system actually switches the battery's mode, within all the safety "
        "limits below — but only when Watch only is OFF and config.yaml is not forcing "
        "watch-only. Takes effect after Apply & restart.",
        applies="restart",
    ),
    SettingsField(
        "control.max_switches_per_day", "Max mode switches/day", "int", 10, "control",
        help="Hard cap on routine battery mode writes per day (SPEC target: under 10). Committed "
        "grid-charge writes use a separate limit below. Ceiling 20.",
        min=1, max=20,
    ),
    SettingsField(
        "control.max_commitment_switches_per_day", "Max commitment switches/day", "int", 4,
        "control",
        help="Hard cap on mode writes for a committed grid-charge (GRID_CHARGE_TO_TARGET) per "
        "local day — independent of the routine cap above, so a spent routine budget cannot block "
        "a planned cheap-charge window (#309).",
        min=1, max=10, advanced=True, applies="live",
    ),
    SettingsField(
        "control.commitment_reserve", "Switches reserved for charging", "int", 3, "control",
        help="Deprecated — no longer affects control (#309). Kept so existing settings load; use "
        "Max commitment switches/day instead.",
        min=0, max=10, advanced=True,
    ),
    SettingsField(
        "control.intent_persistence_cycles", "Confirm before switching", "int", 2, "control",
        help="How many control cycles a routine mode change (e.g. idle↔self-consumption) must "
        "persist before the battery is actually switched — so a brief flip-flop doesn't waste a "
        "switch from the daily budget. The first cycle observes, the rest confirm. 1 = switch "
        "immediately (the original behaviour). Charging commitments, manual overrides, the "
        "car-charging safety hold and returning to the safe automatic mode are never delayed.",
        min=1, max=6, advanced=True,
    ),
    SettingsField(
        "control.min_dwell_seconds", "Min dwell", "number", 600.0, "control",
        help="Minimum seconds to hold a mode before another switch is allowed (floor 60s).",
        min=60.0, max=3600.0, unit="s", advanced=True,
    ),
    SettingsField(
        "control.allow_export_discharge", "Allow export discharge", "bool", False, "control",
        help="Arm live forced DISCHARGE for EXPORT_FOR_PROFIT (trading / deliberate grid export) "
        "only. Off (default) = never dump to the grid for profit. Does not change "
        "DISCHARGE_FOR_LOAD (house-serve stays vendor AUTO). Still needs Watch only off + "
        "operational on, and yaml dry_run / live mode floors. Edited on the Trading page "
        "(confirm-to-arm); omitted from Manage so there is one arming control.",
        advanced=True,
        show_in_settings=False,
    ),
    SettingsField(
        "control.hold_battery_when_car_charging", "Special battery behaviour while the car charges",
        "bool", True, "control",
        help="The master on/off for ALL special battery behaviour while the car is charging. OFF: "
        "the planner runs exactly as it would with no car — the battery is untouched. ON: the "
        "battery follows the 'While the car charges' mode below (Hold by default). Needs the EV "
        "meter configured to detect charging. Re-checked every control cycle.",
    ),
    SettingsField(
        "control.car_charging_battery_mode", "While the car charges", "enum", "hold", "control",
        help="What the home battery does while the car is charging (only when the master switch "
        "above is on). Hold (default): the battery idles so it can't discharge into the car — so "
        "solar and grid cover the car, exactly as before. Discharge a set amount: it discharges "
        "at the fixed wattage below; note that any part of that ABOVE the actual house load DOES "
        "feed the car from the battery — a deliberate choice. Match the house load: the battery "
        "discharges only as much as the house (excluding the car) is predicted to use, so it "
        "quietly covers the house while the grid keeps feeding the car and the battery never ends "
        "up charging the car.",
        options=("hold", "static_discharge", "match_home_load"),
    ),
    SettingsField(
        "control.car_discharge_w", "Car-charging discharge power", "number", 800.0, "control",
        help="How many watts the battery discharges while the car charges, when 'While the car "
        "charges' is set to 'Discharge a set amount'. Above your actual house load this surplus "
        "feeds the car from the battery; use 'Match the house load' if you don't want that. "
        "Ignored by the other two modes.",
        min=100.0, max=5000.0, step=50.0, unit="W",
    ),
    SettingsField(
        "control.car_charging_threshold_w", "Car-charging threshold", "number", 500.0, "control",
        help="EV power above this counts as 'charging' for the special battery behaviour above.",
        min=100.0, max=11000.0, step=100.0, unit="W", advanced=True,
    ),
    SettingsField(
        "control.car_session_end_cycles", "Car session end hysteresis", "int", 3, "control",
        help="How many consecutive control cycles the car must read below the charging threshold "
        "before a discharge session actually ends. Guards against brief power dips (three-phase "
        "balancing, charging ramp pauses) flapping the battery mode on and off. 1 = end "
        "immediately (the original behaviour). Session START stays immediate either way.",
        min=1, max=12, advanced=True,
    ),
    SettingsField(
        "control.live_read_seconds", "Battery/meter read interval", "number", 60.0, "control",
        help="How often the app actually reads the battery + meters (live values are reused in "
        "between). RAISE this if the battery/Indevolt app feels slow or the master drops offline — "
        "the battery's little server is shared with Home Assistant + the Indevolt app, so reading "
        "less often eases the load. Lower = more up-to-the-second dashboard.",
        min=15.0, max=300.0, step=15.0, unit="s", slider=True,
    ),
    SettingsField(
        "control.sample_max_age_seconds", "Max battery-level age", "number", 600.0, "control",
        help="How long a freshly-read battery level (SoC) may still be treated as known. Past this "
        "age EMS treats the level as unknown and will not plan or command on a fabricated empty "
        "battery — it falls back to the battery's own self-use. Keep this at or above the "
        "read interval so a single coalesced read doesn't look stale.",
        min=300.0, max=3600.0, step=60.0, unit="s", slider=True, advanced=True,
    ),
    # Main fuse must be user-confirmed (#197). Default fuse=unset keeps the conservative 1×25 W
    # ceiling for the validator without pretending the connection size is known — System shows a
    # CHECK until the household picks a common NL size or custom watts.
    SettingsField(
        "control.grid_fuse", "Main fuse (hoofdzekering)", "enum", "unset", "control",
        help="Your home's main fuse / netlimiet. EMS rejects a grid-charge plan whose charge "
        "power plus expected house load would exceed this ceiling (hold self-use) so it never "
        "trips the fuse. Pick a common Dutch size, or Custom to enter watts. Until you choose, "
        "EMS keeps the conservative 1×25 A default and flags this as not confirmed on System.",
        options=("unset", "1x25", "1x35", "3x25", "3x35", "custom"),
    ),
    SettingsField(
        "control.grid_limit_w", "Custom fuse limit", "number", 5750.0, "control",
        help="Main fuse ceiling in watts when Main fuse is Custom. Common NL sizes at 230 V: "
        "1×25 A ≈ 5750 W, 1×35 A ≈ 8050 W, 3×25 A ≈ 17250 W, 3×35 A ≈ 24150 W. Set 0 to disable "
        "the check. Preset fuse choices set this automatically.",
        min=0.0, max=50000.0, step=50.0, unit="W",
        visible_when=(("control.grid_fuse", "custom"),),
    ),
    # --- Planner economics (advanced — change these and /api/plan recomputes, SPEC §8.3) ---
    SettingsField(
        "planner.mode", "Planner mode", "enum", "rule_based", "planner",
        help="Which planner produces the schedule the battery follows. Rule-based (default) is the "
        "deterministic summer/winter logic and the only selectable mode today. ML and Advisory are "
        "shown greyed out — they arrive with the optional accelerator ML layer (M6) and stay off "
        "so a Pi never loads GPU code. Every mode still passes the same safety validator before "
        "any write.",
        options=("rule_based", "ml", "advisory"),
        disabled_options=("ml", "advisory"),
        advanced=True,
    ),
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
        "planner.trading_enabled", "Trading (export / buy-to-sell)", "bool", False, "planner",
        help="Opt-in trading mode (E-11). When on, each day compares path T (buy cheap → sell/"
        "export on peaks) vs path Z (self-consumption / house-first). Trading actions run only "
        "when T beats Z by at least the extra-€ threshold below. Off (default) = summer/winter "
        "only — energy stays for the house. Year-round; no watt-tracking. Live forced discharge "
        "still needs the Trading page live-export arm + the usual Watch-only / operational floors.",
    ),
    SettingsField(
        "planner.trading_min_extra_eur", "Trading: min extra €/day", "number", 0.50, "planner",
        help="Path T must project at least this many euros more than path Z before the planner "
        "emits EXPORT_FOR_PROFIT. Default €0.50.",
        min=0.0, max=20.0, step=0.05, unit="€/day",
    ),
    SettingsField(
        "planner.max_export_kwh_per_day", "Trading: max export kWh/day", "number", 0.0, "planner",
        help="Hard cap on deliberate export AC kWh per day. 0 = no hard cap (may empty down to "
        "the SoC reserve). Set a positive value to limit dumps while arming live.",
        min=0.0, max=50.0, step=0.5, unit="kWh",
    ),
    SettingsField(
        "planner.min_export_kwh", "Trading: min export kWh", "number", 0.5, "planner",
        help="Skip export when available surplus is below this — avoids tiny mode switches.",
        min=0.0, max=10.0, step=0.1, unit="kWh", advanced=True,
    ),
    SettingsField(
        "planner.export_mode", "Trading: export slot policy", "enum", "peak_slice", "planner",
        help="How export discharge slots are chosen. Peak slice: the planner picks the best "
        "expensive windows (same style as charge slots). Full dump: empty available surplus "
        "to the SoC reserve across the peak window. Both are mode switches at max power — "
        "not continuous watt-tracking.",
        options=("peak_slice", "full_dump"),
    ),
    SettingsField(
        "planner.max_cycles_per_day", "Max battery cycles / day", "number", 1.5, "planner",
        help=(
            "Canonical runtime cycle budget (overrides the SPEC sample "
            "arbitrage.max_cycles_per_day). Equivalent full cycles per day shared by "
            "load-arbitrage and trading ((kWh charged + kWh discharged) / (2 × usable kWh)). "
            "When exhausted, no more trade that day."
        ),
        min=0.0, max=5.0, step=0.1, unit="EFC", advanced=True,
    ),
    SettingsField(
        "planner.daily_min_savings_eur", "Min projected savings / day", "number", 0.20, "planner",
        help="Canonical runtime twin of arbitrage.daily_min_savings_eur (this key wins when set). "
        "If projected daily savings are below this, enter whole-day no-trade (AUTO) — "
        "applies to load-arbitrage and trading alike.",
        min=0.0, max=10.0, step=0.05, unit="€", advanced=True,
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
    SettingsField(
        "planner.must_charge_enabled", "Voorzorgslading vóór avondpiek", "bool", False, "strategy",
        help="Optioneel: laad de batterij bij tot het ingestelde doel vóór een dure avondpiek "
        "als normale arbitrage geen koopmoment vindt en het laadniveau laag is. Standaard uit.",
    ),
    SettingsField(
        "planner.must_charge_target_soc", "Voorzorgslading: doel laadniveau", "number", 30.0,
        "strategy",
        help="Laadniveau (%) dat voorzorgslading probeert te halen vóór de piek. Blijft binnen "
        "je reserve en batterijgrenzen.",
        min=5.0, max=100.0, step=1.0, unit="%",
    ),
    SettingsField(
        "planner.must_charge_max_price_eur_per_kwh", "Voorzorgslading: prijsplafond", "number",
        0.40, "strategy",
        help="Laad alleen op slots met een bekende prijs tot dit plafond (€/kWh), los van de "
        "arbitrage-max-inkoop.",
        min=0.0, max=2.0, step=0.01, unit="€/kWh", advanced=True,
    ),
    SettingsField(
        "planner.must_charge_buffer_pp", "Voorzorgslading: buffer onder vloer", "number", 10.0,
        "strategy",
        help="Start voorzorgslading alleen wanneer het laadniveau onder de effectieve vloer "
        "plus deze buffer (procentpunt) zit.",
        min=0.0, max=50.0, step=1.0, unit="pp", advanced=True,
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
        help="OpenAI-compatible chat endpoint. Point this at any compatible public provider "
        "(or a zero-retention gateway) — the app isn't locked to one vendor. Private/LAN "
        "and link-local addresses are rejected (#232).",
        advanced=True,
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
        "push notifications with no Apple/Google account and no cloud subscription. "
        "Private LAN URLs need “Allow LAN ntfy server” (#232).",
    ),
    SettingsField(
        "notify.allow_lan_url", "Allow LAN ntfy server", "bool", False, "notify",
        help="Off by default (#232): the ntfy URL may not point at a private/LAN address "
        "(SSRF guard). Turn on only for a self-hosted ntfy on your home network. "
        "Link-local and cloud-metadata addresses stay blocked.",
        advanced=True,
    ),
    SettingsField(
        "notify.ntfy_topic", "ntfy topic", "text", "", "notify",
        help="A private, hard-to-guess topic name — anyone who knows it can read what's "
        "published there, so avoid anything guessable (a random string is safest). Subscribe to "
        "the exact same topic in the ntfy app. Pick a long random topic — anyone who knows it "
        "can read these notifications on ntfy.sh. Must not contain /, ?, or # (#232).",
    ),
    SettingsField(
        "notify.device_unreachable_minutes", "Device-down push after (minutes)", "int", 15,
        "notify",
        help="Send a push when the P1 meter or battery has had no fresh reading for this many "
        "minutes (issue #128). Default 15. Notify-only — never writes to the battery. Recovery "
        "push waits for 10 minutes of stable contact so short flaps don't spam.",
        min=5, max=120, step=1, unit="min",
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
        "ev.departure_time", "Usual departure time (legacy)", "text", "07:30", "ev",
        help="Legacy single daily departure for the deprecated GET /api/advisor/ev-charge only. "
        "The Car plan uses each schedule day's ready_by instead. See docs/legacy-retirement.md "
        "(B-85).",
        advanced=True,
    ),
    SettingsField(
        "ev.charge_kwh", "Typical energy to add", "number", 20.0, "ev",
        help="Roughly how much energy a typical top-up adds. Also used as the winter-planner "
        "EV-day size when the EV day hint or today's schedule day is on (#181 — advice only).",
        min=1.0, max=100.0, step=1.0, unit="kWh",
    ),
    SettingsField(
        "ev.day_hint", "EV day hint (winter sizing)", "bool", False, "ev",
        help="Mark today as an EV-import day so the winter planner sizes battery top-up / peak "
        "readiness for ~ev.charge_kwh of car load (#181). Advice/forecast only — never controls "
        "the charger. Off + no schedule/history ⇒ fail-soft (no EV addend).",
    ),
    SettingsField(
        "ev.expected_day_kwh", "Expected EV import today", "number", 0.0, "ev",
        help="Optional manual override for today's expected car import (kWh). When > 0, the "
        "winter planner uses this instead of the day hint / schedule / same-weekday history "
        "(#181). 0 = auto. Advice only — no charger writes. Does not unblock B-17 / #105.",
        min=0.0, max=100.0, step=1.0, unit="kWh", advanced=True,
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
    # --- Consumers (appliances) — #223 / epic #222; opt-in device ids + start advice (#227) ---
    SettingsField(
        "consumers.dry_run", "Watch only (no appliance starts)", "bool", True, "consumers",
        help="ON (safe default): show start-window advice only — EMS never starts an appliance. "
        "OFF alone does not start anything either; live delayed-start needs an explicit arming "
        "control that is not shipped yet (#227). Same safety feel as battery Watch only / trading.",
        show_in_settings=False,
    ),
    SettingsField(
        "consumers.start_advice_enabled",
        "Appliance start-window advice",
        "bool",
        True,
        "consumers",
        help="When on, Insights shows the cheapest contiguous price window for opted-in appliances "
        "that support delayed start and are idle. Advice only — never starts a cycle.",
        show_in_settings=False,
    ),
    SettingsField(
        "consumers.default_duration_slots", "Default cycle length", "int", 4, "consumers",
        help="How long a typical wash/dry/dish cycle is assumed to run, in 15-minute slots. "
        "Default 4 = 1 hour. Used only for start-window advice sizing.",
        min=1.0, max=32.0, unit="slots", show_in_settings=False, advanced=True,
    ),
    SettingsField(
        "consumers.quiet_hours",
        "Quiet hours (no start advice)",
        "text",
        "",
        "consumers",
        help="Optional local-time window when start-window advice must not begin or end, e.g. "
        "22:00-07:00. Empty = no restriction (#333).",
        show_in_settings=False,
        advanced=True,
    ),
    SettingsField(
        "consumers.preferred_windows",
        "Per-appliance preferred windows",
        "text",
        "{}",
        "consumers",
        help='JSON map of device id → local "HH:MM-HH:MM" band where advice should fit. '
        "Empty object = no per-device preference (#333).",
        show_in_settings=False,
        advanced=True,
    ),
    SettingsField(
        "consumers.smartthings.enabled", "SmartThings integration", "bool", False, "consumers",
        help="Turn on the Samsung SmartThings appliance adapter (washer/dryer). "
        "Needs a personal access token below. Read-only — EMS does not start cycles yet. "
        "Takes effect after Apply & restart (same as other connection tokens).",
        show_in_settings=False, applies="restart",
    ),
    SettingsField(
        "consumers.smartthings.pat", "SmartThings personal access token", "secret", "", "consumers",
        help="Personal access token from https://account.smartthings.com/tokens "
        "(devices:read). Stored locally; never logged. Env: SMARTTHINGS_PAT. "
        "Takes effect after Apply & restart.",
        show_in_settings=False, applies="restart",
    ),
    SettingsField(
        "consumers.smartthings.device_ids", "SmartThings devices (opt-in)", "text",
        default_device_ids_json(), "consumers",
        help="JSON list of SmartThings device ids that may participate. Default [] = none. "
        "Edited when the consumers UI lands (#224); not a battery write path.",
        show_in_settings=False,
    ),
    SettingsField(
        "consumers.ha.enabled", "Home Assistant appliances", "bool", False, "consumers",
        help="Turn on the Home Assistant appliance adapter (read-only entity status). "
        "Needs URL + long-lived access token. Takes effect after Apply & restart.",
        show_in_settings=False, applies="restart",
    ),
    SettingsField(
        "consumers.ha.url", "Home Assistant URL", "text", "", "consumers",
        help="Base URL of Home Assistant, e.g. http://homeassistant.local:8123. "
        "Env: HA_URL. Takes effect after Apply & restart.",
        show_in_settings=False, applies="restart",
    ),
    SettingsField(
        "consumers.ha.token", "Home Assistant access token", "secret", "", "consumers",
        help="Long-lived access token (HA profile → Security). Stored locally; never logged. "
        "Env: HA_TOKEN. Takes effect after Apply & restart.",
        show_in_settings=False, applies="restart",
    ),
    SettingsField(
        "consumers.ha.device_ids", "Home Assistant entities (opt-in)", "text",
        default_device_ids_json(), "consumers",
        help="JSON list of HA entity ids that may participate (e.g. switch.washer). "
        "Default [] = none. Opt-in selection for the appliances insight.",
        show_in_settings=False,
    ),
    SettingsField(
        "consumers.mock.device_ids", "Mock consumer devices (opt-in)", "text",
        default_device_ids_json(), "consumers",
        help="JSON list of mock appliance ids for tests/dev. Default [] = none.",
        show_in_settings=False, advanced=True,
    ),
    # --- Appearance ---
    SettingsField(
        "ui.theme", "Theme", "enum", "auto", "ui",
        help="Dashboard colour theme.", options=("auto", "dark", "light"),
    ),
)

SETTINGS_BY_KEY: dict[str, SettingsField] = {f.key: f for f in SETTINGS_SCHEMA}
SECRET_KEYS: frozenset[str] = frozenset(f.key for f in SETTINGS_SCHEMA if f.type == "secret")

# NL main-fuse presets @ 230 V → total watts (`control.grid_limit_w`). `unset` / `custom` are not
# listed: unset keeps the conservative 5750 W schema default; custom uses the number field.
GRID_FUSE_PRESET_W: dict[str, float] = {
    "1x25": 5750.0,
    "1x35": 8050.0,
    "3x25": 17250.0,
    "3x35": 24150.0,
}
_GRID_W_TO_FUSE: dict[float, str] = {w: k for k, w in GRID_FUSE_PRESET_W.items()}


def fuse_from_watts(watts: float) -> str:
    """Map a stored watt ceiling to a fuse token (exact preset match, else `custom`)."""
    return _GRID_W_TO_FUSE.get(float(watts), "custom")


def defaults() -> dict[str, Any]:
    """The default value of every setting (the config.yaml-equivalent baseline)."""
    return {f.key: f.default for f in SETTINGS_SCHEMA}


def _normalise_dev_mode(dev_mode: str | None) -> str | None:
    if dev_mode is None:
        return None
    return str(dev_mode).strip().lower()


def _mock_forecast_provider_disabled(dev_mode: str | None) -> bool:
    """#242: synthetic mock forecast is demo-only — grey out / reject in live Settings.

    ``dev_mode=None`` is intentionally permissive (tolerant read paths such as
    ``effective_settings``) — HTTP boundaries must pass the real ``dev.mode``.
    """
    mode = _normalise_dev_mode(dev_mode)
    if mode is None:
        return False
    return mode not in ("mock", "replay")


def live_mock_forecast_provider_active(values: dict[str, Any], dev_mode: str | None) -> bool:
    """True when a live/replay-excluded install still has mock persisted as the solar provider."""
    if not _mock_forecast_provider_disabled(dev_mode):
        return False
    return values.get("solar.forecast_provider") == "mock"


def live_mock_forecast_in_use(
    solar_forecast: object | None,
    values: dict[str, Any],
    dev_mode: str | None,
) -> bool:
    """True while a live install is still planning on the built-in demo solar curve (#242).

    Covers (a) persisted ``solar.forecast_provider=mock`` and (b) the restart window after the
    operator saved Forecast.Solar/Solcast but ``build_wiring`` has not rebuilt the source yet —
    ``settings_cache`` already shows the new provider while ``slots()`` is still mock.
    """
    if live_mock_forecast_provider_active(values, dev_mode):
        return True
    if not _mock_forecast_provider_disabled(dev_mode):
        return False
    # Lazy import: settings stays import-pure; forecast module registers adapters.
    from ems.sources.forecast import MockSolarForecastSource

    return isinstance(solar_forecast, MockSolarForecastSource)


# Call sites that validate settings without ``dev_mode`` (must stay tolerant / internal-only).
VALIDATE_SETTINGS_WITHOUT_DEV_MODE = frozenset({
    "effective_settings",
    "plan_preview",
    "replay",
    "whatif",
})


def _schema_disabled_options(field: SettingsField, dev_mode: str | None) -> tuple[str, ...]:
    extra: tuple[str, ...] = ()
    if field.key == "solar.forecast_provider" and _mock_forecast_provider_disabled(dev_mode):
        extra = ("mock",)
    return tuple(dict.fromkeys(field.disabled_options + extra))


def _registry_enum_options(key: str, fallback: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """Overlay hard-coded enum options with live adapter-registry names (#113).

    Lazy imports keep `settings` import-time pure (no vendor network). Unknown domains keep the
    schema fallback so a missing registration never blanks the UI.
    """
    if key == "solar.forecast_provider":
        try:
            # Importing the factory registers adapters (lazy builders — no I/O).
            from ems.sources.forecast_factory import registered_providers
        except Exception:  # pragma: no cover — defensive; schema must still render
            return fallback
        names = registered_providers()
        return names if names else fallback
    if key == "reporting.carbon_signal":
        try:
            from ems.sources.carbon_factory import registered_providers
        except Exception:  # pragma: no cover
            return fallback
        names = registered_providers()
        return names if names else fallback
    return fallback


def schema_json(dev_mode: str | None = None) -> list[dict]:
    """Serialize the schema for the UI to render a form generically.

    Keys with `show_in_settings=False` stay in SETTINGS_SCHEMA (defaults, validation, POST)
    but are omitted here so Manage does not render a duplicate control.

    ``dev_mode`` (mock/replay vs live) gates demo-only enum tokens such as ``mock`` forecast (#242).
    """
    rows: list[dict] = []
    for f in SETTINGS_SCHEMA:
        if not f.show_in_settings:
            continue
        opts = _registry_enum_options(f.key, f.options)
        disabled = _schema_disabled_options(f, dev_mode)
        rows.append({
            "key": f.key, "label": f.label, "type": f.type, "default": f.default,
            "group": f.group, "help": f.help, "min": f.min, "max": f.max,
            "options": list(opts) if opts else None, "step": f.step, "unit": f.unit,
            "advanced": f.advanced, "applies": f.applies, "slider": f.slider,
            "visible_when": {k: v for k, v in f.visible_when} if f.visible_when else None,
            "disabled_options": list(disabled),
        })
    return rows


def field_visible(field: SettingsField | dict[str, Any], values: dict[str, Any]) -> bool:
    """True when `field` should be shown given current (edited) setting values.

    Accepts either a `SettingsField` or a `schema_json()` row. Missing keys in `values` are
    treated as non-matching (field hidden) so a partially loaded form never flashes gated fields.
    """
    if isinstance(field, SettingsField):
        rules = field.visible_when
    else:
        raw = field.get("visible_when") or {}
        rules = tuple(raw.items()) if isinstance(raw, dict) else ()
    if not rules:
        return True
    for key, need in rules:
        if values.get(key) != need:
            return False
    return True


def _coerce(
    field: SettingsField, value: Any, *, dev_mode: str | None = None,
) -> tuple[bool, Any]:
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
        opts = _registry_enum_options(field.key, field.options) or ()
        if value not in opts:
            return False, f"must be one of: {', '.join(opts)}"
        disabled = _schema_disabled_options(field, dev_mode)
        if value in disabled:
            if field.key == "solar.forecast_provider" and value == "mock":
                allowed = [o for o in opts if o not in disabled]
                hint = " or ".join(repr(o) for o in allowed) if allowed else repr(field.default)
                return False, (
                    f"{value!r} is only for demo/mock run mode (use {hint})"
                )
            return False, (f"{value!r} is not available yet "
                           f"(arrives in a later release; use {field.default!r})")
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


def validate_settings(
    partial: Any, *, dev_mode: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate a partial settings dict. Returns (clean, errors): only valid keys land in `clean`,
    every rejected key gets a human-readable message in `errors`. Unknown keys are errors.

    A secret submitted as "" means "leave unchanged" — it is dropped from `clean` (not stored).
    Legacy ``grid_fees.tibber_total_includes_all`` is accepted as an alias for
    ``grid_fees.price_includes_all_fees`` for one release (#119); the canonical key wins if both
    are present."""
    if not isinstance(partial, dict):
        return {}, {"_": "expected a JSON object of setting keys to values"}
    from ems.tariffs import (
        LEGACY_PRICE_INCLUDES_ALL_FEES_KEY,
        PRICE_INCLUDES_ALL_FEES_KEY,
    )

    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    # #232: ntfy private-LAN opt-in must be known before validating notify.ntfy_url
    # (dict key order is not guaranteed for a multi-key save).
    allow_lan_ntfy = False
    if "notify.allow_lan_url" in partial:
        ok_lan, lan_val = _coerce(
            SETTINGS_BY_KEY["notify.allow_lan_url"], partial["notify.allow_lan_url"],
            dev_mode=dev_mode,
        )
        if ok_lan:
            allow_lan_ntfy = bool(lan_val)
    for key, value in partial.items():
        canonical = (
            PRICE_INCLUDES_ALL_FEES_KEY
            if key == LEGACY_PRICE_INCLUDES_ALL_FEES_KEY
            else key
        )
        # Canonical already set from the new key — ignore a redundant legacy write.
        if (
            key == LEGACY_PRICE_INCLUDES_ALL_FEES_KEY
            and PRICE_INCLUDES_ALL_FEES_KEY in clean
        ):
            continue
        field = SETTINGS_BY_KEY.get(canonical)
        if field is None:
            errors[key] = "unknown setting"
            continue
        key = canonical
        ok, result = _coerce(field, value, dev_mode=dev_mode)
        if not ok:
            errors[key] = result
            continue
        if key == "notify.ntfy_url" and result:
            from ems.url_safety import normalize_outbound_http_url

            try:
                clean[key] = normalize_outbound_http_url(
                    str(result), allow_private=allow_lan_ntfy, allow_path=False,
                )
            except ValueError as exc:
                errors[key] = str(exc)
            continue
        if key == "notify.ntfy_topic" and result:
            from ems.url_safety import normalize_ntfy_topic

            try:
                clean[key] = normalize_ntfy_topic(str(result))
            except ValueError as exc:
                errors[key] = str(exc)
            continue
        if key == "explainer.base_url" and result:
            from ems.url_safety import normalize_outbound_http_url

            try:
                clean[key] = normalize_outbound_http_url(
                    str(result), allow_private=False, allow_path=True,
                )
            except ValueError as exc:
                errors[key] = str(exc)
            continue
        if key in (
            "meters.p1_ip", "meters.solar_ip", "meters.car_ip", "battery.indevolt_ip",
        ) and result:
            from ems.url_safety import normalize_device_host

            try:
                clean[key] = normalize_device_host(str(result))
            except ValueError as exc:
                errors[key] = str(exc)
            continue
        if key == "battery.indevolt_ips_extra" and result:
            from ems.url_safety import normalize_device_host

            parts = [p.strip() for p in str(result).split(",")]
            try:
                normalised = [normalize_device_host(p) for p in parts if p.strip()]
            except ValueError as exc:
                errors[key] = str(exc)
                continue
            clean[key] = ", ".join(normalised)
            continue
        if key == "consumers.ha.url" and result:
            # #266 / #261 / #263: HA is typically LAN — allow private hosts, but require a
            # sane http(s) base (no file:, empty host, userinfo, or path/query smuggling).
            try:
                clean[key] = normalize_ha_url(str(result))
            except ValueError as exc:
                errors[key] = str(exc)
            continue
        if key == "consumers.quiet_hours":
            from ems.consumers.start_advice import quiet_hours_error

            text = str(result or "").strip()
            err = quiet_hours_error(text)
            if err:
                errors[key] = err
                continue
            clean[key] = text
            continue
        if key == "consumers.preferred_windows":
            from ems.consumers.start_advice import (
                parse_preferred_windows_json,
                preferred_windows_error,
            )

            raw = str(result or "").strip() or "{}"
            err = preferred_windows_error(raw)
            if err:
                errors[key] = err
                continue
            parsed = parse_preferred_windows_json(raw)
            clean[key] = json.dumps(parsed, sort_keys=True) if parsed else "{}"
            continue
        if field.type == "secret" and result == "":
            continue  # blank secret = keep the existing value
        clean[key] = result
    # Fuse presets own the watt ceiling — keep `control.grid_limit_w` aligned on save so the
    # validator and UI never disagree with the chosen size (#197).
    if "control.grid_fuse" in clean:
        fuse = clean["control.grid_fuse"]
        if fuse in GRID_FUSE_PRESET_W:
            clean["control.grid_limit_w"] = GRID_FUSE_PRESET_W[fuse]
        elif fuse == "unset":
            clean["control.grid_limit_w"] = float(SETTINGS_BY_KEY["control.grid_limit_w"].default)
    # #341: do not invent prices.export_model_confirmed here. effective_settings() re-validates
    # the whole store on read; confirmation is applied only by POST /api/settings.
    return clean, errors


def settings_migration_writes(stored: Any) -> dict[str, Any]:
    """Persist-side migrations for renamed keys / floors. Empty when nothing to write."""
    if not isinstance(stored, dict):
        return {}
    from ems.battery_limits import DEFAULT_MIN_TARGET_SOC_PCT
    from ems.tariffs import (
        LEGACY_PRICE_INCLUDES_ALL_FEES_KEY,
        PRICE_INCLUDES_ALL_FEES_KEY,
    )

    out: dict[str, Any] = {}
    if (
        PRICE_INCLUDES_ALL_FEES_KEY not in stored
        and LEGACY_PRICE_INCLUDES_ALL_FEES_KEY in stored
    ):
        out[PRICE_INCLUDES_ALL_FEES_KEY] = stored[LEGACY_PRICE_INCLUDES_ALL_FEES_KEY]
    # #245: legacy installs could store reserve 0–4%; clamp up to the device SoC floor so
    # effective_settings does not drop the key and snap to the 10% default.
    if "battery.min_reserve_soc" in stored:
        try:
            reserve = float(stored["battery.min_reserve_soc"])
        except (TypeError, ValueError):
            reserve = None
        if reserve is not None and reserve < DEFAULT_MIN_TARGET_SOC_PCT:
            out["battery.min_reserve_soc"] = DEFAULT_MIN_TARGET_SOC_PCT
    # #232: preserve an already-configured private/LAN ntfy URL by enabling the opt-in
    # rather than silently dropping the URL on the new SSRF guard.
    if (
        stored.get("notify.ntfy_url")
        and "notify.allow_lan_url" not in stored
        and "notify.allow_lan_url" not in out
    ):
        from urllib.parse import urlparse

        from ems.url_safety import hostname_is_private_ip

        try:
            host = (urlparse(str(stored["notify.ntfy_url"])).hostname or "").lower()
        except ValueError:
            host = ""
        # Only true home-LAN forms — not cloud ``.internal`` / metadata names (#232 Grok M2).
        if host and host not in ("localhost", "metadata", "instance-data") and (
            hostname_is_private_ip(host)
            or host.endswith((".local", ".lan", ".localhost"))
        ) and not host.endswith(".internal"):
            out["notify.allow_lan_url"] = True
    return out


def effective_settings(stored: Any) -> dict[str, Any]:
    """Defaults overlaid by the valid subset of `stored`. Invalid/unknown stored values are
    silently dropped on read (tolerant) — a bad persisted row must never break the dashboard.

    #197: an install that already stored `control.grid_limit_w` without `control.grid_fuse` is
    treated as confirmed (infer preset or custom from watts). A missing watts row stays `unset`
    so we never pretend the unintentional 1×25 schema default was a deliberate choice.

    #119: legacy ``grid_fees.tibber_total_includes_all`` overlays the neutral key when present.
    """
    eff = defaults()
    raw = dict(stored) if isinstance(stored, dict) else {}
    raw.update(settings_migration_writes(raw))
    clean, _errors = validate_settings(raw)
    if "control.grid_fuse" not in clean and "control.grid_limit_w" in clean:
        clean["control.grid_fuse"] = fuse_from_watts(float(clean["control.grid_limit_w"]))
    fuse = clean.get("control.grid_fuse", eff["control.grid_fuse"])
    if fuse in GRID_FUSE_PRESET_W:
        clean["control.grid_limit_w"] = GRID_FUSE_PRESET_W[fuse]
    eff.update(clean)
    from ems.battery_profile import apply_topology_defaults
    return apply_topology_defaults(eff)


def public_values(values: dict[str, Any]) -> dict[str, Any]:
    """Effective values with secrets masked (never leak a token over the API). A masked secret is
    reported as "" plus a parallel `<key>.__set` boolean is added so the UI can show 'set'."""
    out = dict(values)
    for key in SECRET_KEYS:
        was_set = bool(out.get(key))
        out[key] = ""
        out[f"{key}.__set"] = was_set
    return out
