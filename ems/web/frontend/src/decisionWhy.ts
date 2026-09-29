// B-33 / #85 slice 1: turn the /api/battery-plan DecisionReason into a 1–2 sentence Dutch
// "waarom" line for grid charge / hold / discharge. Facts come from the reason object (#84);
// wording is assembled here so dry-run can say "zou" instead of claiming a live write.
// Slice 2/3 (reserve/pause/EV) stay out of this module.

import { eur } from "./format";

/** Slice-1 battery actions only — laden / vasthouden / ontladen. */
export const SLICE1_BATTERY_ACTIONS = ["grid_charge", "hold", "discharge"] as const;
export type Slice1BatteryAction = (typeof SLICE1_BATTERY_ACTIONS)[number];

export function isSlice1BatteryAction(action: string): action is Slice1BatteryAction {
  return (SLICE1_BATTERY_ACTIONS as readonly string[]).includes(action);
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

const ACTION_LABEL_NL: Record<Slice1BatteryAction, string> = {
  grid_charge: "Laden van het net",
  hold: "Vasthouden",
  discharge: "Ontladen",
};

/** Short Dutch label for the current slice-1 action chip. */
export function slice1ActionLabel(action: Slice1BatteryAction): string {
  return ACTION_LABEL_NL[action];
}

function actionSentence(action: Slice1BatteryAction, dryRun: boolean, reason: DecisionReason): string {
  const window = reason.chosen_window;
  const priceHint =
    window?.eur_per_kwh_min != null && Number.isFinite(window.eur_per_kwh_min)
      ? ` (vanaf ${eur(window.eur_per_kwh_min)}/kWh)`
      : "";

  if (dryRun) {
    switch (action) {
      case "grid_charge":
        return `EMS zou nu laden van het net${priceHint}.`;
      case "hold":
        return "EMS zou de batterij vasthouden tot een beter moment.";
      case "discharge":
        return "EMS zou ontladen om dure netstroom te vermijden.";
    }
  }
  switch (action) {
    case "grid_charge":
      return `EMS laadt nu van het net${priceHint}.`;
    case "hold":
      return "EMS houdt de batterij vast tot een beter moment.";
    case "discharge":
      return "EMS ontlaadt om dure netstroom te vermijden.";
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

/**
 * Build the homeowner "waarom" text for a slice-1 battery action.
 * Returns null when the action is out of slice-1 scope or reason is missing.
 */
export function formatBatteryActionWhy(
  reason: DecisionReason | null | undefined,
  action: string,
  opts: { dryRun: boolean },
): string | null {
  if (!reason || !isSlice1BatteryAction(action)) return null;
  const first = actionSentence(action, opts.dryRun, reason);
  const second = benefitOrSafetySentence(reason);
  return `${first} ${second}`;
}
