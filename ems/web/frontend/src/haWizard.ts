/** Pure helpers for the Home Assistant connection wizard (#263). */

export type HaWizardStep =
  | "status"
  | "url"
  | "create_token"
  | "paste_token"
  | "test"
  | "pick"
  | "done";

export type HaProbeDevice = {
  id: string;
  name: string;
  kind: string;
};

export type HaProbeResult = {
  ok: boolean;
  url?: string;
  devices?: HaProbeDevice[];
  summary?: string;
  error?: string;
  warning?: string;
  message?: string;
};

const PRIVATE_HOST =
  /^(localhost|127\.0\.0\.1|\[::1\]|.*\.local|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)$/i;

/** Trim + strip a trailing slash. Does not validate scheme (see validateHaUrlClient). */
export function trimHaUrl(raw: string): string {
  return raw.trim().replace(/\/+$/, "");
}

export type HaUrlClientCheck = {
  ok: boolean;
  normalized: string;
  error?: string;
  httpLanWarning?: boolean;
};

/** Client-side URL shape check before calling the probe. */
export function validateHaUrlClient(raw: string): HaUrlClientCheck {
  const normalized = trimHaUrl(raw);
  if (!normalized) {
    return { ok: false, normalized: "", error: "Enter your Home Assistant address." };
  }
  let parsed: URL;
  try {
    parsed = new URL(normalized);
  } catch {
    return {
      ok: false,
      normalized,
      error: "That doesn’t look like a URL. Try http://homeassistant.local:8123.",
    };
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return {
      ok: false,
      normalized,
      error: "Use http:// or https:// at the start of the address.",
    };
  }
  if (!parsed.hostname) {
    return { ok: false, normalized, error: "The address needs a host name." };
  }
  if (parsed.username || parsed.password) {
    return {
      ok: false,
      normalized,
      error: "Don’t put a username or password in the address — use a long-lived token instead.",
    };
  }
  if (parsed.pathname && parsed.pathname !== "/") {
    return {
      ok: false,
      normalized,
      error: "Use only the base address (no /api path).",
    };
  }
  if (parsed.search || parsed.hash) {
    return {
      ok: false,
      normalized,
      error: "Use only the base address (no ?query or #fragment).",
    };
  }
  const host = parsed.hostname;
  const httpLanWarning =
    parsed.protocol === "http:" && !PRIVATE_HOST.test(host);
  return {
    ok: true,
    normalized: `${parsed.protocol}//${parsed.host}`,
    httpLanWarning,
  };
}

export function tokenCreateUrl(haBase: string): string {
  const base = trimHaUrl(haBase);
  return `${base}/profile/security`;
}

export function probeErrorMessage(result: HaProbeResult | null, fallback = "Something went wrong."): string {
  if (!result) return fallback;
  if (result.message) return result.message;
  return fallback;
}

export const KIND_LABEL: Record<string, string> = {
  washer: "Washing machine",
  dryer: "Dryer",
  dishwasher: "Dishwasher",
  other: "Appliance",
  unknown: "Appliance",
};

/** Next step after a successful connect when devices were found. */
export function stepAfterSuccessfulTest(deviceCount: number): HaWizardStep {
  return deviceCount > 0 ? "pick" : "pick";
}
