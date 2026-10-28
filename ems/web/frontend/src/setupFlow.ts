/** Onboarding wizard helpers (#291) — pure logic for unit tests. */

export const SETUP_STEP_STORAGE_KEY = "ems.setup.step";
export const SETUP_BATTERY_IP_KEY = "ems.setup.batteryIp";
export const SETUP_P1_IP_KEY = "ems.setup.p1Ip";

export const P1_RESTART_MESSAGE =
  "EMS start opnieuw om je P1-meter te zoeken. Dit duurt even, laat dit scherm open.";

const PRIVATE_V4_HINT =
  "Gebruik een privé IPv4-adres (10.x.x.x, 172.16–172.31.x.x of 192.168.x.x).";

export function needsP1IpField(currentP1Ip: string | undefined | null): boolean {
  return !String(currentP1Ip ?? "").trim();
}

export function buildBatteryLinkPayload(
  batteryIp: string,
  p1Ip: string,
  currentP1FromSettings: string | undefined | null,
): Record<string, string | boolean> {
  const payload: Record<string, string | boolean> = {
    "battery.indevolt_ip": batteryIp.trim(),
    "connection.use_live_devices": true,
  };
  if (needsP1IpField(currentP1FromSettings) && p1Ip.trim()) {
    payload["meters.p1_ip"] = p1Ip.trim();
  }
  return payload;
}

/** P1 wizard (#292): only meters.p1_ip when the value changes; null = restart-only. */
export function buildP1LinkPayload(
  p1Ip: string,
  currentP1FromSettings: string | undefined | null,
): Record<string, string> | null {
  const trimmed = p1Ip.trim();
  const current = String(currentP1FromSettings ?? "").trim();
  if (trimmed && trimmed !== current) {
    return { "meters.p1_ip": trimmed };
  }
  if (current && (!trimmed || trimmed === current)) {
    return null;
  }
  return { "meters.p1_ip": trimmed };
}

function parseIpv4Octets(host: string): number[] | null {
  const parts = host.split(".");
  if (parts.length !== 4) return null;
  const nums: number[] = [];
  for (const p of parts) {
    if (!/^\d+$/.test(p)) return null;
    const n = Number(p);
    if (n < 0 || n > 255) return null;
    nums.push(n);
  }
  return nums;
}

function isPrivateIpv4(host: string): boolean {
  const o = parseIpv4Octets(host.trim());
  if (!o) return false;
  const [a, b] = o;
  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  return false;
}

/** Client-side hint only — server validation is authoritative. */
export function privateIpv4Hint(
  value: string,
  field: "battery" | "p1" = "battery",
): string | null {
  const v = value.trim();
  if (!v) {
    return field === "p1"
      ? "Vul het IP-adres van je P1-meter in."
      : "Vul het IP-adres van je batterij in.";
  }
  if (v.includes(":") || v.includes("/") || v.includes("://")) {
    return PRIVATE_V4_HINT;
  }
  if (!isPrivateIpv4(v)) return PRIVATE_V4_HINT;
  return null;
}

export function persistSetupStep(step: string | null): void {
  try {
    if (step) sessionStorage.setItem(SETUP_STEP_STORAGE_KEY, step);
    else sessionStorage.removeItem(SETUP_STEP_STORAGE_KEY);
  } catch {
    /* private mode / quota — best effort */
  }
}

/** Drop wizard persistence after a terminal outcome so a later #setup visit stays fresh. */
export function clearSetupPersistence(): void {
  persistSetupStep(null);
  try {
    sessionStorage.removeItem(SETUP_BATTERY_IP_KEY);
    sessionStorage.removeItem(SETUP_P1_IP_KEY);
  } catch {
    /* best effort */
  }
}

export function readPersistedSetupStep(): string | null {
  try {
    return sessionStorage.getItem(SETUP_STEP_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function persistBatteryIp(ip: string): void {
  try {
    sessionStorage.setItem(SETUP_BATTERY_IP_KEY, ip);
  } catch {
    /* best effort */
  }
}

export function readPersistedBatteryIp(): string {
  try {
    return sessionStorage.getItem(SETUP_BATTERY_IP_KEY) ?? "";
  } catch {
    return "";
  }
}

export function persistP1Ip(ip: string): void {
  try {
    sessionStorage.setItem(SETUP_P1_IP_KEY, ip);
  } catch {
    /* best effort */
  }
}

export function readPersistedP1Ip(): string {
  try {
    return sessionStorage.getItem(SETUP_P1_IP_KEY) ?? "";
  } catch {
    return "";
  }
}

export type BatteryWizardPhase =
  | "form"
  | "restarting"
  | "success"
  | "error"
  | "manual_restart";

export function batteryChecklistOutcome(
  batteryStatus: string | undefined,
  savedIp: string,
): { phase: BatteryWizardPhase; message: string } | null {
  if (batteryStatus === "Werkt") {
    return {
      phase: "success",
      message: "EMS ziet je batterij. Je kunt verder met de volgende stap.",
    };
  }
  if (savedIp.trim()) {
    return {
      phase: "error",
      message:
        `Batterij niet gevonden op ${savedIp.trim()}. `
        + "Staan de batterij en EMS op hetzelfde netwerk?",
    };
  }
  return null;
}

export type P1ChecklistOutcome = {
  phase: BatteryWizardPhase;
  message: string;
  linkBattery?: boolean;
};

export function p1ChecklistOutcome(
  p1Status: string | undefined,
  batteryStatus: string | undefined,
  savedIp: string,
): P1ChecklistOutcome | null {
  if (p1Status === "Werkt") {
    return {
      phase: "success",
      message: "EMS ziet je P1-meter. Je kunt verder met de volgende stap.",
    };
  }
  if (batteryStatus === "Ontbreekt") {
    return {
      phase: "error",
      message: "Koppel eerst je batterij",
      linkBattery: true,
    };
  }
  const ip = savedIp.trim();
  if (ip) {
    return {
      phase: "error",
      message:
        `P1-meter niet gevonden op ${ip}. `
        + "Staan de meter en EMS op hetzelfde netwerk?",
    };
  }
  return null;
}

export const RESTART_POLL_MS = 1500;
export const RESTART_POLL_TIMEOUT_MS = 60000;

export function restartPollConfig(): { intervalMs: number; timeoutMs: number } {
  const inj =
    typeof window !== "undefined"
      ? (window as unknown as {
          __EMS_RESTART_POLL__?: { intervalMs?: number; timeoutMs?: number };
        }).__EMS_RESTART_POLL__
      : undefined;
  return {
    intervalMs: typeof inj?.intervalMs === "number" ? inj.intervalMs : RESTART_POLL_MS,
    timeoutMs: typeof inj?.timeoutMs === "number" ? inj.timeoutMs : RESTART_POLL_TIMEOUT_MS,
  };
}

export type RestartDispatch =
  | { kind: "started"; bootId: string }
  | { kind: "busy" }
  | { kind: "not_supervised" }
  | { kind: "error"; message: string };

/** Map POST /api/system/restart response for the battery wizard (#291 AC 11). */
export function interpretRestartResponse(
  status: number,
  body: Record<string, unknown>,
): RestartDispatch {
  if (status === 202 && typeof body.boot_id === "string") {
    return { kind: "started", bootId: body.boot_id };
  }
  if (status === 409) {
    if (body.reason === "not_supervised") {
      return { kind: "not_supervised" };
    }
    return { kind: "busy" };
  }
  const detail = typeof body.detail === "string" ? body.detail : `HTTP ${status}`;
  return { kind: "error", message: detail };
}
