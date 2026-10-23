// #364 — which Settings drafts arm live control, and the homeowner labels for that step.
// Copy lives in labels.ts; this module only decides when it applies.

import {
  CONTROL_ARM_CONSEQUENCE,
  CONTROL_SECTION_HINT,
  MODE_BLOCK_RECOVERY,
  RESTART_PENDING_LABEL,
} from "./labels";

export type ControlDraftValue = number | boolean | string | undefined;

const WATCH_ONLY_KEY = "control.dry_run";
const OPERATIONAL_KEY = "control.operational";

/**
 * True when the draft turns Watch only off (dry_run true → false) or turns
 * "Let the system control the battery" on (operational false → true).
 * The safer direction does not confirm.
 */
export function draftNeedsControlConfirm(
  saved: Readonly<Record<string, ControlDraftValue>>,
  draft: Readonly<Record<string, ControlDraftValue>>,
): boolean {
  const watchOnlyOff = saved[WATCH_ONLY_KEY] === true && draft[WATCH_ONLY_KEY] === false;
  const operationalOn = saved[OPERATIONAL_KEY] === false && draft[OPERATIONAL_KEY] === true;
  return watchOnlyOff || operationalOn;
}

/** Badge text for a restart-tagged field. Pending replaces the `restart_pending` token. */
export function restartPendingStatusLabel(pending: boolean): string {
  return pending ? RESTART_PENDING_LABEL : "needs restart";
}

/** Save-bar line after a successful save. */
export function savedApplyStatusText(restartPending: boolean): string {
  return restartPending
    ? `Saved — ${RESTART_PENDING_LABEL} (boot still runs old values until Apply & restart)`
    : "Saved — active now";
}

export function controlArmConsequenceText(): string {
  return CONTROL_ARM_CONSEQUENCE;
}

export function controlSectionHint(): string {
  return CONTROL_SECTION_HINT;
}

export function modeBlockRecovery(): { sentence: string; more: string } {
  return MODE_BLOCK_RECOVERY;
}
