/**
 * B-99 — topbar status chip budget.
 *
 * Healthy live sessions keep at most 1–2 meaningful chips (run-mode / attention).
 * Redundant "Alles actueel" and "Live sensoren" are demoted when DeviceHealth or
 * run-mode already conveys safety. Confidence stays on the hero, never as a fourth
 * chrome badge.
 */

/** Data-quality chip only when attention is needed (not complete). */
export function showTopbarDataQuality(dataQuality: string | null | undefined): boolean {
  return Boolean(dataQuality && dataQuality !== "complete");
}

/**
 * Demote "Live sensoren" when `dev_mode === "live"` — run-mode + DeviceHealth already
 * say the house is on real meters. Keep Demo / mock / replay as attention.
 */
export function showTopbarDataSource(devMode: string | null | undefined): boolean {
  return Boolean(devMode && devMode !== "live");
}

export type TopbarStatusInput = {
  /** Status payload present (run-mode chip always renders when so). */
  hasStatus: boolean;
  dry_run_reason?: string | null;
  dev_mode?: string | null;
  data_quality?: string | null;
};

/** Testids of topbar status chips that would render for this input. */
export function topbarStatusChipTestIds(input: TopbarStatusInput): string[] {
  const ids: string[] = [];
  if (input.hasStatus) ids.push("run-mode-badge");
  if (input.dry_run_reason) ids.push("run-mode-reason");
  if (showTopbarDataSource(input.dev_mode)) ids.push("data-source");
  if (showTopbarDataQuality(input.data_quality)) ids.push("data-quality");
  return ids;
}
