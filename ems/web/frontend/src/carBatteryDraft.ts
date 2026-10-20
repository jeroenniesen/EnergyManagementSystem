// #365 — pure draft for "While the car charges". The radio cards and wattage stay local until
// the Car view's existing save bar posts them; nothing here talks to the network.

export type CarChargingMode = "hold" | "static_discharge" | "match_home_load";

export type CarBatteryDraft = {
  mode: CarChargingMode;
  dischargeW: number;
};

export const CAR_MODE_MIN_W = 100;
export const CAR_MODE_MAX_W = 5000;
export const CAR_MODE_STEP_W = 50;

/** Step and clamp a typed wattage. A non-number keeps the saved fallback. */
export function clampDischargeW(raw: number, fallback: number): number {
  if (!Number.isFinite(raw)) return fallback;
  const stepped = Math.round(raw / CAR_MODE_STEP_W) * CAR_MODE_STEP_W;
  return Math.max(CAR_MODE_MIN_W, Math.min(CAR_MODE_MAX_W, stepped));
}

/**
 * One sentence, shown before Save, naming what the home battery will do while the car charges.
 * Fixed power includes the wattage Save will post (already clamped by the caller).
 */
export function carBatteryConsequence(draft: CarBatteryDraft): string {
  switch (draft.mode) {
    case "hold":
      return "The home battery holds and does not discharge while the car charges.";
    case "static_discharge":
      return `The home battery discharges at a fixed ${draft.dischargeW} W while the car charges.`;
    case "match_home_load":
      return "The home battery covers the house automatically while the car charges.";
  }
}

export type CarBatteryDraftDiff = { mode: boolean; dischargeW: boolean };

/** Which draft fields differ from the last saved values. Wattage is compared after clamp. */
export function carBatteryDraftDiff(saved: CarBatteryDraft, draft: CarBatteryDraft): CarBatteryDraftDiff {
  return {
    mode: saved.mode !== draft.mode,
    dischargeW: saved.dischargeW !== clampDischargeW(draft.dischargeW, saved.dischargeW),
  };
}

/**
 * Settings body for one POST. Both keys travel together whenever either field is dirty,
 * so a mode change never lands without its wattage. Unchanged → null (leave the body alone).
 */
export function carBatterySettingsBody(
  saved: CarBatteryDraft,
  draft: CarBatteryDraft,
): {
  "control.car_charging_battery_mode": CarChargingMode;
  "control.car_discharge_w": number;
} | null {
  const diff = carBatteryDraftDiff(saved, draft);
  if (!diff.mode && !diff.dischargeW) return null;
  return {
    "control.car_charging_battery_mode": draft.mode,
    "control.car_discharge_w": clampDischargeW(draft.dischargeW, saved.dischargeW),
  };
}

/** Live comparison against the number in the field — not the last blurred/saved wattage. */
export function showAboveHouseWarning(
  mode: CarChargingMode,
  enteredW: number,
  houseLoadW: number | null,
): boolean {
  return (
    mode === "static_discharge" &&
    Number.isFinite(enteredW) &&
    houseLoadW != null &&
    enteredW > houseLoadW
  );
}
