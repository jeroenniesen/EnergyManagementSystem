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

/** Plain-language confidence behind the current plan, keyed by data-quality level. */
export const CONFIDENCE: Record<string, string> = {
  complete: "High confidence — all data is fresh.",
  degraded: "Good — some non-critical data is delayed or estimated.",
  price_fallback: "Using a fallback price curve — price-based moves are paused.",
  unsafe: "Plan paused until battery/meter data returns; the battery is safe.",
};

/** System-view overall health badge (same palette as data quality). Sentence-case "Check" so this
 * chip and the per-check-row STATUS_LABEL ("Check") read as the same vocabulary — a shouty
 * all-caps "NEEDS A LOOK" (this label is upper-cased by CSS, like every badge) came across as an
 * alarm in production screenshots for something that's calm-safe, not broken. */
export const SYSTEM_OVERALL: Record<string, Labelled> = {
  ok: { label: "All good", title: "Every readiness check passed." },
  warn: { label: "Check", title: "One or more checks want attention, but nothing is broken." },
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

/** The dashboard's car badge (App.tsx), keyed by the controller's `desired_mode` (from
 * /api/decision) — feat/car-charge-modes made the badge mode-aware, since the guard now has three
 * behaviours instead of always holding. `desired_mode: "discharge"` here reliably means the narrow
 * car-session mapping is active (`intent_to_mode(..., car_session=True)`, SPEC §7.1) — the operator
 * picked `static_discharge`/`match_home_load` and the battery is actually covering the house — so
 * it's the one token worth a distinct phrase; every other mode (idle/auto/charge) is the safe hold,
 * today's default, byte-for-byte. Fall back to CAR_BADGE_SUFFIX_DEFAULT for anything unmapped. */
export const CAR_BADGE_SUFFIX: Record<string, string> = {
  discharge: "battery covering the house",
};
export const CAR_BADGE_SUFFIX_DEFAULT = "battery held";

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

/** Control-incident types (from /api/incidents), in plain words — see export_package.incident_rollup. */
export const INCIDENT_TYPE_LABEL: Record<string, string> = {
  cluster_mismatch: "Cluster mismatch",
  command_failed: "Command failed",
  fallback: "Fell back to a safe default",
  revert: "Reverted to safe mode",
};

/** Production feedback ("don't know what action I need to take here"): each incident TYPE gets a
 * short, honest "what to do" line under its count row on the System page (B-37 parity) — covers
 * every key in INCIDENT_TYPE_LABEL above. command_failed/cluster_mismatch point at something
 * concrete to check; fallback/revert are EMS protecting itself, so the copy stays reassuring
 * rather than inventing a fix — only recurring incidents warrant a look. */
export const INCIDENT_TYPE_ACTION: Record<string, string> = {
  command_failed:
    "The battery didn't confirm some commands; EMS retried or fell back safely. If this keeps " +
    "growing, check the Indevolt gateway's power/network.",
  cluster_mismatch:
    "A tower is running its own mode — power-cycle it if it hasn't rejoined after a few cycles.",
  fallback:
    "EMS chose a safe default rather than risk an uncertain command — no action needed unless " +
    "this keeps recurring.",
  revert:
    "EMS reverted to the battery's own safe mode to protect your home — no action needed unless " +
    "this keeps recurring.",
};

/** Model-health track verdict (B-76, from /api/accuracy's `health` block) → dot colour + text
 * label (never colour-only) + a title for the "still collecting evidence" honest empty state.
 * "Check" (not "Needs a look") matches the System page's check-row STATUS_LABEL convention. */
export const HEALTH_STATUS: Record<"ok" | "warn" | "unknown", Labelled> = {
  ok: { label: "Working well", title: "The recent evidence looks dependable." },
  warn: { label: "Check", title: "There is something worth reviewing below." },
  unknown: { label: "Still collecting evidence", title: "Not enough history yet to judge this." },
};

/** Row titles for the Model-health panel (B-76), keyed the same as /api/accuracy's health block. */
export const HEALTH_ROW_LABEL: Record<"solar" | "load" | "plan_execution", string> = {
  solar: "Solar outlook",
  load: "Home energy pattern",
  plan_execution: "Plan follow-through",
};

/** Plan-provenance line (feat/ux-batch-3): which planner FUNCTION produced the live plan, in plain
 * words, keyed the same as /api/battery-plan's `provenance.planner`. "rule_based" only ever occurs
 * for the winter arbitrage planner; "adaptive"/"summer" only ever occur for the summer solar-first
 * strategy (ems.web.api's `_resolved_planner_name` mirrors ems.planner.strategy.build_plan's own
 * dispatch), so the season is safely folded into the copy without a separate field. */
export const PLANNER_PROVENANCE_LABEL: Record<string, string> = {
  rule_based: "rule-based winter planner",
  adaptive: "adaptive summer planner",
  summer: "solar-first summer planner",
};

/** Scenario/ML planning-intelligence layer status (CLAUDE.md honesty ask, feat/ux-batch-3):
 * ems/intelligence/planning.py builds pessimistic/expected/optimistic planning scenarios (E-08),
 * but it is NOT wired into live planning — it validates in the background against real outcomes,
 * it never steers a plan. `/api/battery-plan`'s `provenance.intelligence` (backend constant
 * `ems.web.api.INTELLIGENCE_MODE`) is the SOURCE OF TRUTH: BatteryPlan.tsx's inline provenance
 * fragment reads that LIVE value through this map's `short` text. System.tsx's standalone
 * "Planning intelligence" row does not fetch /api/battery-plan, so it reads
 * `CURRENT_INTELLIGENCE_MODE` below (this map's `label`/`detail`) instead of a second hardcoded
 * sentence. Either way there is exactly ONE place to flip when a mode starts actually steering a
 * plan: add its entry here, point `CURRENT_INTELLIGENCE_MODE` at it, and flip
 * `ems.web.api.INTELLIGENCE_MODE` to match. */
export const INTELLIGENCE_COPY: Record<string, { label: string; detail: string; short: string }> = {
  shadow: {
    label: "Planning intelligence",
    detail: "validating in shadow; the dependable baseline plans today",
    short: "validating, not steering yet",
  },
};
export const CURRENT_INTELLIGENCE_MODE: keyof typeof INTELLIGENCE_COPY = "shadow";

// --- Now / Next / Why drawer (2026-07-15 plan) ------------------------------------------------
// Pure homeowner copy for the "What is EMS doing?" drawer. Kept decoupled from App types (plain
// structural input) so it stays a pure, unit-testable function with no import cycle.
export type NowDrawerInputs = {
  currentAction: string | null; // battery-plan current_action (grid_charge/solar_charge/…)
  intent: string | null; // decision intent, the fallback when there's no plan action yet
  why: string | null; // the already-explained plan reason
  targetSocPct: number | null;
  plannedTopupKwh: number | null;
  overrideActive: boolean;
  actText: string; // the hero's already-derived "do I need to act?" line (kept consistent)
  actCalm: boolean;
};
export type NowDrawerCopy = {
  happened: string;
  why: string;
  next: string;
  action: string;
  calm: boolean;
};

// Homeowner phrasing for the current action — battery-plan action first, decision intent as a
// fallback, so the drawer reads plainly even before the plan endpoint has answered.
const NOW_HAPPENED_BY_ACTION: Record<string, string> = {
  grid_charge: "Charging the battery from the grid",
  solar_charge: "Storing today's solar in the battery",
  discharge: "Your battery is powering your home",
  self_consume: "Your battery is powering your home",
  hold: "Holding your stored energy in reserve",
  paused: "Running on the battery's own safe mode",
};
const NOW_HAPPENED_BY_INTENT: Record<string, string> = {
  grid_charge_to_target: "Charging the battery from the grid",
  discharge_for_load: "Your battery is powering your home",
  allow_self_consumption: "Your battery is powering your home",
  hold_reserve: "Holding your stored energy in reserve",
};

export function nowDrawerCopy(i: NowDrawerInputs): NowDrawerCopy {
  const happened =
    (i.currentAction ? NOW_HAPPENED_BY_ACTION[i.currentAction] : undefined) ??
    (i.intent ? NOW_HAPPENED_BY_INTENT[i.intent] : undefined) ??
    "EMS is following the current plan";
  const why = i.why?.trim() || "EMS is following the current plan for your home.";
  let next: string;
  if (i.overrideActive) {
    next = "You're directing the battery manually — it returns to the plan when the override ends.";
  } else if (i.plannedTopupKwh != null && i.plannedTopupKwh > 0) {
    next = "Charging is planned for the lower-cost period ahead.";
  } else if (i.targetSocPct != null) {
    next = `Aiming to keep the battery near ${Math.round(i.targetSocPct)}% on the current plan.`;
  } else {
    next = "No change is planned right now — EMS keeps following the plan.";
  }
  const action = i.actCalm ? "No action needed." : i.actText;
  return { happened, why, next, action, calm: i.actCalm };
}

// --- Confidence drawer (2026-07-15 plan) ------------------------------------------------------
// Plain-language meaning of the plan-confidence level, and the always-true safety net line.
export const CONFIDENCE_MEANING: Record<"high" | "medium" | "low", string> = {
  high: "Recent forecasts are tracking well, so today's plan is dependable.",
  medium: "Useful for planning, but cautious — EMS leans conservative where it isn't sure.",
  low: "The safe baseline is active — EMS is holding the battery's own safe mode rather than "
    + "planning aggressively.",
};
export const CONFIDENCE_SAFETY =
  "Your battery is never put at risk: whenever EMS isn't confident, it falls back to the "
  + "battery's own safe self-consumption mode.";
