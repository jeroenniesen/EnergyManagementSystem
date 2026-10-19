/**
 * B-99 — topbar status chip budget.
 *
 * Healthy live sessions keep at most 1–2 meaningful chips (live-writing / attention).
 * #314 merges the old run-mode badge and the separate cause chip into one
 * `liveWritingChip`. Redundant "Alles actueel" and "Live sensoren" stay demoted.
 * Confidence stays on the hero, never as a fourth chrome badge.
 */

import {
  LIVE_WRITING,
  LIVE_WRITING_CAUSE,
  LIVE_WRITING_CONDITION,
} from "./labels";

/** Data-quality chip only when attention is needed (not complete). */
export function showTopbarDataQuality(dataQuality: string | null | undefined): boolean {
  return Boolean(dataQuality && dataQuality !== "complete");
}

/**
 * Demote "Live sensoren" when `dev_mode === "live"` — the live-writing chip + DeviceHealth
 * already say the house is on real meters. Keep Demo / mock / replay as attention.
 */
export function showTopbarDataSource(devMode: string | null | undefined): boolean {
  return Boolean(devMode && devMode !== "live");
}

export type TopbarStatusInput = {
  /** Kept for callers. The live-writing chip still renders when status is missing (fail-safe NEE). */
  hasStatus: boolean;
  dry_run_reason?: string | null;
  dev_mode?: string | null;
  data_quality?: string | null;
};

/** Testids of topbar status chips that would render for this input. */
export function topbarStatusChipTestIds(input: TopbarStatusInput): string[] {
  const ids: string[] = ["run-mode-badge"];
  if (showTopbarDataSource(input.dev_mode) && !showTopbarDataQuality(input.data_quality)) ids.push("data-source");
  if (showTopbarDataQuality(input.data_quality)) ids.push("data-quality");
  return ids;
}

export type LiveWritingState = "yes" | "no" | "starting";

/** Slice of `/api/status` the chip and the Settings panel need. Missing fields fail safe to NEE. */
export type LiveWritingStatus = {
  dry_run?: boolean | null;
  dry_run_cause?: string | null;
  dry_run_reason?: string | null;
  live_writing?: boolean | null;
  live_writing_state?: string | null;
  dev_mode?: string | null;
};

export type LiveWritingChipModel = {
  label: string;
  title: string;
  /** `data-cause` value; omitted when there is no cause (live JA, or bare NEE). */
  cause: string | null;
  state: LiveWritingState;
};

function resolveLiveWritingState(status: LiveWritingStatus): LiveWritingState {
  // Contradictory or incomplete payloads fail closed. JA requires every new field to agree.
  if (status.dry_run === true) return "no";
  if (status.dry_run_cause === "observing" && status.dry_run === false) {
    return "starting";
  }
  if (status.dry_run === false && status.dry_run_cause == null
      && status.live_writing === true && status.live_writing_state === "yes"
      && status.dev_mode === "live") {
    return "yes";
  }
  return "no";
}

function causeCode(status: LiveWritingStatus, state: LiveWritingState): string | null {
  if (state === "starting") return "observing";
  if (state !== "no") return null;
  if (status.dry_run_cause) return status.dry_run_cause;
  if (status.dev_mode === "mock" || status.dev_mode === "replay") return "mock";
  return null;
}

/**
 * One status chip (#314). Pure: no I/O.
 *
 * - JA only when the status says writes are live and this is not the startup grace.
 * - "NEE — oorzaak" for the watch-only floors (demo, config.yaml, Instellingen, …).
 * - Opstarten (1–2 min) during observing — different copy from Watch-only.
 * - Missing/unknown status → bare "Live schrijven: NEE".
 *
 * `idleReason` (last `/api/decision` reason) is the tooltip only while state is `no`.
 */
export function liveWritingChip(
  status: LiveWritingStatus | null | undefined,
  idleReason?: string | null,
): LiveWritingChipModel {
  if (status == null) {
    return {
      label: LIVE_WRITING.no,
      title: LIVE_WRITING.noTitle,
      cause: null,
      state: "no",
    };
  }
  const state = resolveLiveWritingState(status);
  if (state === "starting") {
    return {
      label: LIVE_WRITING.starting,
      title: status.dry_run_reason?.trim() || LIVE_WRITING.startingTitle,
      cause: "observing",
      state: "starting",
    };
  }
  if (state === "yes") {
    return {
      label: LIVE_WRITING.yes,
      title: LIVE_WRITING.yesTitle,
      cause: null,
      state: "yes",
    };
  }
  const cause = causeCode(status, state);
  const suffix = cause ? LIVE_WRITING_CAUSE[cause] : undefined;
  const idle = idleReason?.trim();
  return {
    label: suffix ? `${LIVE_WRITING.no} — ${suffix}` : LIVE_WRITING.no,
    title: idle || status.dry_run_reason?.trim() || LIVE_WRITING.noTitle,
    cause,
    state: "no",
  };
}

export type LiveWritingConditionId = keyof typeof LIVE_WRITING_CONDITION;

export type LiveWritingCondition = {
  id: LiveWritingConditionId;
  label: string;
  detail: string;
  ok: boolean;
  section: string;
  highlight: string | null;
};

function asBool(value: unknown): boolean {
  return value === true;
}

function filled(value: unknown): boolean {
  return typeof value === "string" && value.trim().length > 0;
}

function secretSet(values: Record<string, unknown>, key: string): boolean {
  if (values[`${key}.__set`] === true) return true;
  return filled(values[key]);
}

function yamlFloorOk(status: LiveWritingStatus | null | undefined): boolean {
  if (!status) return false;
  if (status.dev_mode === "mock" || status.dev_mode === "replay") return false;
  if (status.dry_run_cause === "config_dry_run" || status.dry_run_cause === "mock") return false;
  return status.dev_mode === "live";
}

/**
 * Read-only checklist for Settings (#314). ✓ only when that floor is actually clear.
 * Unknown values are ✗. This function never writes settings.
 */
export function liveWritingConditions(
  values: Record<string, unknown> | null | undefined,
  status: LiveWritingStatus | null | undefined,
): LiveWritingCondition[] {
  const v = values ?? {};
  const yamlOk = yamlFloorOk(status);
  const watchOk = v["control.dry_run"] === false;
  const armedOk = asBool(v["control.operational"]);
  const devicesOk =
    asBool(v["connection.use_live_devices"])
    && filled(v["meters.p1_ip"])
    && filled(v["battery.indevolt_ip"]);
  const pricesOk =
    asBool(v["connection.use_live_prices"]) && secretSet(v, "prices.tibber_token");
  const rows: Array<Omit<LiveWritingCondition, "label" | "detail"> & { id: LiveWritingConditionId }> = [
    { id: "yaml", ok: yamlOk, section: "control", highlight: null },
    { id: "watch_only", ok: watchOk, section: "control", highlight: "control.dry_run" },
    { id: "operational", ok: armedOk, section: "control", highlight: "control.operational" },
    { id: "devices", ok: devicesOk, section: "connection", highlight: "connection.use_live_devices" },
    { id: "prices", ok: pricesOk, section: "prices", highlight: "prices.tibber_token" },
  ];
  return rows.map((row) => {
    const copy = LIVE_WRITING_CONDITION[row.id];
    return { ...row, label: copy.label, detail: row.ok ? copy.ok : copy.fail };
  });
}
