// Plain-language labels for the backend's internal enums and signal keys, so a homeowner never
// sees a raw token like "unsafe", "dry_run" or "solcast: stale". Each map has a humanize() fallback
// so an unmapped value still reads as a phrase ("grid_charge_to_target" → "Grid charge to target")
// instead of leaking snake_case to the screen. Shared by the dashboard, override and system views.

/** Turn a snake_case / kebab token into a readable, sentence-cased phrase. */
export function humanize(token: string): string {
  const words = token.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : token;
}

type Labelled = { label: string; title: string };

/** Run mode: is the system actually commanding the battery, or only watching? */
export const RUN_MODE: Record<"dry" | "live", Labelled> = {
  dry: {
    label: "Watching only",
    title: "The system shows what it would do, but never changes the battery.",
  },
  live: {
    label: "Controlling battery",
    title: "The system is actively switching the battery between modes.",
  },
};

/** Data source: real sensors vs. the built-in simulator. */
export const DATA_SOURCE: Record<"live" | "sim", Labelled> = {
  live: { label: "Live sensors", title: "Readings come from your real meters and battery." },
  sim: { label: "Demo data", title: "Readings come from the built-in simulator, not your home." },
};

/** Overall data quality / system health → friendly phrase + explanation. */
export const DATA_QUALITY: Record<string, Labelled> = {
  complete: { label: "All data current", title: "All sensors and forecasts are fresh." },
  degraded: {
    label: "Some data delayed",
    title: "Some sensor or forecast data is stale, so the plan may be less precise.",
  },
  price_fallback: {
    label: "Using backup prices",
    title: "Live prices are unavailable, so a fallback price curve is in use.",
  },
  unsafe: {
    label: "Paused — safe mode",
    title: "Data is missing or stale, so the system fell back to the battery's own safe mode.",
  },
};

/** System-view overall health badge (same palette as data quality). */
export const SYSTEM_OVERALL: Record<string, Labelled> = {
  ok: { label: "All good", title: "Every readiness check passed." },
  warn: { label: "Needs a look", title: "One or more checks want attention, but nothing is broken." },
  fail: { label: "Problem", title: "A readiness check failed — see the list below." },
};

/** Friendly names for the per-signal freshness keys. */
export const SIGNAL_NAME: Record<string, string> = {
  grid: "Grid meter",
  solar: "Solar meter",
  soc: "Battery level",
  battery: "Battery",
  ev: "Car charger",
  tibber_price: "Prices",
  prices: "Prices",
  solar_forecast: "Solar forecast",
  solcast: "Solar forecast",
  forecast: "Solar forecast",
};

/** Freshness states in plain words. */
export const FRESHNESS_STATE: Record<string, string> = {
  fresh: "up to date",
  stale: "delayed",
  missing: "unavailable",
};

/** Controller decision outcomes in plain words (every token the controller/API can emit). */
export const OUTCOME_LABEL: Record<string, string> = {
  dry_run: "Watching only",
  not_controlling: "Watching only",
  applied: "Applied",
  idempotent: "No change needed",
  dwell: "Waiting before switching",
  cap_reached: "Daily switch limit reached",
  no_plan: "No plan yet",
  unconfigured: "Not set up",
  failed_recovered: "Recovered — safe mode",
  failed_unrecovered: "Error — safe mode",
};

/** The battery's physical running mode, in plain words. */
export const PHYSICAL_MODE: Record<string, string> = {
  auto: "Self-use (auto)", // vendor self-consumption / P1-zeroing
  charge: "Charging",
  discharge: "Powering the house",
  idle: "Holding",
};
