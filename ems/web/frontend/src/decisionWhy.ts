// B-33 / #85: turn the /api/battery-plan DecisionReason into a 1–2 sentence Dutch "waarom"
// line. Slice 1 = laden / vasthouden / zelfconsumptie / volle-snelheid. Slice 2 = reserve,
// gepauzeerde staten, bekende vermogensoverschrijding, onbekende capability-grens.
// Facts come from the reason object (#84); dry-run says "zou" instead of claiming a live write.
// Slice 3 (EV) stays out of this module.
//
// Terminology (product language):
// - `discharge` (from discharge_for_load → vendor AUTO) = **zelfconsumptie**, not forced dump.
// - `full_speed_discharge` = true forced discharge = **op volle snelheid ontladen**.

import { eur } from "./format";

/** Literal #85 criterion 5 — known power exceedance pause. */
export const POWER_EXCEEDS_WHY =
  "Gepauzeerd. Het plan vroeg meer vermogen dan je batterij aankan, daarom houdt de batterij haar eigen stand aan.";

/** Literal #85 criterion 6 — unknown capability, cautious continue (charge vs discharge). */
export function capabilityUnknownWhy(dryRun: boolean, action?: string): string {
  const charging =
    action == null
    || action === "grid_charge"
    || action === "solar_charge";
  if (charging) {
    return dryRun
      ? "Zou langzamer laden dan gepland. EMS weet nu niet zeker hoeveel je batterij aankan en zou daarom voorzichtig laden."
      : "Laadt langzamer dan gepland. EMS weet nu niet zeker hoeveel je batterij aankan en laadt daarom voorzichtig.";
  }
  return dryRun
    ? "Zou langzamer ontladen dan gepland. EMS weet nu niet zeker hoeveel je batterij aankan en zou daarom voorzichtig ontladen."
    : "Ontlaadt langzamer dan gepland. EMS weet nu niet zeker hoeveel je batterij aankan en ontlaadt daarom voorzichtig.";
}

/** Slice-1 battery actions that get a Dutch waarom sentence. */
export const SLICE1_BATTERY_ACTIONS = [
  "grid_charge",
  "hold",
  "discharge",
  "full_speed_discharge",
] as const;
export type Slice1BatteryAction = (typeof SLICE1_BATTERY_ACTIONS)[number];

/** Slice-2 homeowner actions (reserve / pause) plus slice-1. */
export const WHY_BATTERY_ACTIONS = [
  ...SLICE1_BATTERY_ACTIONS,
  "paused",
  "self_consume",
] as const;
export type WhyBatteryAction = (typeof WHY_BATTERY_ACTIONS)[number];

export function isSlice1BatteryAction(action: string): action is Slice1BatteryAction {
  return (SLICE1_BATTERY_ACTIONS as readonly string[]).includes(action);
}

export function isWhyBatteryAction(action: string): action is WhyBatteryAction {
  return (WHY_BATTERY_ACTIONS as readonly string[]).includes(action);
}

export type DecisionReason = {
  chosen_window: {
    start: string | null;
    end: string | null;
    intent: string | null;
    label: string | null;
    eur_per_kwh_min: number | null;
    eur_per_kwh_max: number | null;
  } | null;
  rejected_alternative: {
    intent: string | null;
    reason: string;
    window_start: string | null;
    window_end: string | null;
  } | null;
  expected_benefit: { eur: number | null; summary: string } | null;
  risk: { margin_eur_per_kwh: number | null; summary: string } | null;
  safety_constraint: {
    code: string | null;
    message: string | null;
    action: "paused" | "proceed" | string;
  };
  gates: {
    validator_code: string | null;
    failsafe: boolean;
    dwell: boolean;
    cap_reached: boolean;
    unconfirmed: boolean;
  };
  summary: string;
};

/**
 * Homeowner "Nu: …" labels. `discharge` is self-consumption (Indevolt covering house load),
 * not a forced full-power dump — that is `full_speed_discharge`.
 */
const ACTION_LABEL_NL: Record<string, string> = {
  grid_charge: "Laden van het net",
  hold: "Vasthouden",
  discharge: "Zelfconsumptie",
  full_speed_discharge: "Op volle snelheid ontladen",
  self_consume: "Zelfconsumptie",
  self_consumption: "Zelfconsumptie",
  solar_charge: "Laden van zonnepanelen",
  paused: "Gepauzeerd",
  idle: "Inactief",
};

/** Short Dutch label for the current action chip (slice-1 and beyond). */
export function batteryActionLabel(
  action: string | null | undefined,
  reason?: DecisionReason | null,
): string {
  if (!action) return "plan";
  // `hold` is only emitted for hold_reserve (_INTENT_ACTION) — always Reservebescherming,
  // even when chosen_window still points at a charge block in a mixed winter plan.
  void reason;
  if (action === "hold") {
    return "Reservebescherming";
  }
  return ACTION_LABEL_NL[action] ?? action.replace(/_/g, " ");
}

/** @deprecated Prefer batteryActionLabel — kept for slice-1 call sites. */
export function slice1ActionLabel(action: Slice1BatteryAction): string {
  return batteryActionLabel(action);
}

function actionSentence(action: Slice1BatteryAction, dryRun: boolean, reason: DecisionReason): string {
  const window = reason.chosen_window;
  const priceHint =
    window?.eur_per_kwh_min != null && Number.isFinite(window.eur_per_kwh_min)
      ? ` (vanaf ${eur(window.eur_per_kwh_min)}/kWh)`
      : "";
  // `hold` action token ≡ hold_reserve — always reserve-protection copy.
  void window?.intent;

  if (dryRun) {
    switch (action) {
      case "grid_charge":
        return `EMS zou nu laden van het net${priceHint}.`;
      case "hold":
        return "EMS zou de reserve beschermen en de batterij niet verder ontladen.";
      case "discharge":
        return "EMS zou in zelfconsumptie de woning voeden om dure netstroom te vermijden.";
      case "full_speed_discharge":
        return "EMS zou nu op volle snelheid ontladen.";
    }
  }
  switch (action) {
    case "grid_charge":
      return `EMS laadt nu van het net${priceHint}.`;
    case "hold":
      return "EMS beschermt de reserve: de batterij blijft boven je minimumreserve.";
    case "discharge":
      return "EMS laat de batterij in zelfconsumptie de woning voeden om dure netstroom te vermijden.";
    case "full_speed_discharge":
      return "EMS ontlaadt nu op volle snelheid.";
  }
}

function benefitOrSafetySentence(reason: DecisionReason): string {
  const safety = reason.safety_constraint;
  // Safety-first: paused / failsafe / explicit safety message without a positive € benefit.
  const benefitEur = reason.expected_benefit?.eur;
  const hasPositiveBenefit =
    typeof benefitEur === "number" && Number.isFinite(benefitEur) && benefitEur > 0;

  if (safety.action === "paused" || reason.gates.failsafe) {
    return "Dit gaat om veiligheid, niet om besparing.";
  }
  if (hasPositiveBenefit) {
    return `Verwacht voordeel ≈ ${eur(benefitEur)}.`;
  }
  // Honest when the plan carries no positive arbitrage estimate.
  return "Dit gaat om veiligheid.";
}

function safetyCode(reason: DecisionReason): string | null {
  return reason.safety_constraint.code || reason.gates.validator_code || null;
}

/** Paused / degraded waarom sentences (#85 slice 2) — 1–2 sentences. */
function pausedWhy(reason: DecisionReason, dryRun: boolean): string {
  const code = safetyCode(reason);
  if (code === "power_exceeds_capability") {
    return POWER_EXCEEDS_WHY;
  }
  if (code === "stale_inputs" || code === "data_stale") {
    const first = dryRun
      ? "EMS zou pauzeren omdat meetgegevens ontbreken of te oud zijn."
      : "EMS is gepauzeerd omdat meetgegevens ontbreken of te oud zijn.";
    return `${first} De batterij houdt haar eigen stand aan.`;
  }
  // Generic validator-unsafe / failsafe pause.
  const first = dryRun
    ? "EMS zou pauzeren omdat het plan niet veilig genoeg is."
    : "EMS is gepauzeerd omdat het plan niet veilig genoeg is.";
  return `${first} De batterij houdt haar eigen stand aan.`;
}

function overrideWhy(dryRun: boolean): string {
  return dryRun
    ? "Handmatige override is actief. EMS zou jouw keuze volgen in plaats van het automatische plan."
    : "Handmatige override is actief. EMS volgt jouw keuze in plaats van het automatische plan.";
}

/**
 * Build the homeowner "waarom" text for slice-1/2 battery actions.
 * Returns null when the action is out of scope or reason is missing.
 */
export function formatBatteryActionWhy(
  reason: DecisionReason | null | undefined,
  action: string,
  opts: { dryRun: boolean; overrideActive?: boolean },
): string | null {
  if (!reason) return null;

  const code = safetyCode(reason);

  // Criterion 5 — exact literal when known power exceedance paused the plan.
  if (code === "power_exceeds_capability" && reason.safety_constraint.action === "paused") {
    return POWER_EXCEEDS_WHY;
  }
  // Criterion 6 — charge-path literal only. Hold / zelfconsumptie keep their own waarom;
  // the warn is plan-scoped and must not rewrite every hour's sentence.
  if (
    code === "capability_unknown_conservative"
    && (action === "grid_charge" || action === "solar_charge")
  ) {
    return capabilityUnknownWhy(opts.dryRun, action);
  }
  // Incomplete prices: hold self-use; waarom even when action is not "paused".
  if (code === "incomplete_prices" || code === "prices_incomplete") {
    const first = opts.dryRun
      ? "EMS zou pauzeren omdat de stroomprijzen onvolledig zijn."
      : "EMS is gepauzeerd omdat de stroomprijzen onvolledig zijn.";
    return `${first} Zonder actuele prijzen stuurt EMS niet live.`;
  }

  // Override copy only when the override was accepted (not held by validation).
  if (
    opts.overrideActive
    && action !== "paused"
    && reason.safety_constraint.action !== "paused"
  ) {
    return overrideWhy(opts.dryRun);
  }

  if (action === "paused" || reason.safety_constraint.action === "paused") {
    return pausedWhy(reason, opts.dryRun);
  }

  if (!isSlice1BatteryAction(action) && action !== "self_consume") {
    return null;
  }

  // self_consume uses the same zelfconsumptie sentence as discharge.
  const sliceAction: Slice1BatteryAction =
    action === "self_consume" ? "discharge" : (action as Slice1BatteryAction);

  const first = actionSentence(sliceAction, opts.dryRun, reason);
  const second = benefitOrSafetySentence(reason);
  return `${first} ${second}`;
}
