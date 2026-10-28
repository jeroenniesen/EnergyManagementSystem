/** One-click setting applies (#337 / F-36, F-38).

Both buttons POST the existing `/api/settings` writer. They never send a control command and
never include `control.dry_run` or `control.operational`.
*/

/** Mirrors `WHATIF_ALLOWED_KEYS` in `ems/web/routes/whatif.py`. */
export const WHATIF_LIVE_KEYS = [
  "planner.solar_confidence",
  "battery.min_reserve_soc",
  "planner.negative_price_soak",
  "prices.export_price_model",
  "strategy.summer_max_topup_price",
] as const;

const WHATIF_LIVE_KEY_SET = new Set<string>(WHATIF_LIVE_KEYS);

/** Starlette lowercases this. Value must be `1` or the server treats the body as a normal save. */
export const WHATIF_APPLY_HEADER = "X-EMS-Whatif-Apply";

const NEVER_LIVE = new Set(["control.dry_run", "control.operational"]);

export type SolarApply = { key: string; value: number; current?: number };

/** Digest button: exactly `{ "planner.solar_confidence": <recommended> }`, or null. */
export function digestSolarSettingsBody(
  apply: SolarApply | null | undefined,
): { "planner.solar_confidence": number } | null {
  if (!apply || apply.key !== "planner.solar_confidence") return null;
  if (typeof apply.value !== "number" || !Number.isFinite(apply.value)) return null;
  return { "planner.solar_confidence": apply.value };
}

/** What-if confirm: a copy of `overrides` only when every key is allow-listed. Otherwise null
 * (the UI must not POST). The server repeats this check and returns 422. */
export function whatifLiveSettingsBody(
  overrides: Record<string, unknown> | null | undefined,
): Record<string, unknown> | null {
  if (!overrides) return null;
  const keys = Object.keys(overrides);
  if (keys.length === 0) return null;
  for (const key of keys) {
    if (NEVER_LIVE.has(key) || !WHATIF_LIVE_KEY_SET.has(key)) return null;
  }
  return { ...overrides };
}
