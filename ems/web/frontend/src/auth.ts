// Access token for mutating requests. Stored in localStorage so it survives reloads; sent as a
// Bearer header on writes. Reads never need it (the guest dashboard is always open).
const KEY = "ems.token";

export function getToken(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(KEY, token);
    else localStorage.removeItem(KEY);
  } catch {
    /* storage disabled — header just won't be sent */
  }
}

// Drop the stored token (logout, or the global 401 handler discovering it's no longer valid).
export function clearToken(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* storage disabled — nothing to clear */
  }
}

export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

// A 401 anywhere (any authenticated /api call) needs to bounce the user back to the login screen.
// The app registers exactly one handler for this (see App.tsx) — apiFetch just needs somewhere to
// report it without importing App.tsx (which would be circular).
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

// Use for ALL authenticated same-origin /api calls (never for /api/auth/login, /api/auth/onboard —
// those are exempt endpoints handled by Login/Onboarding directly, or for non-/api/external fetches
// like OSM map tiles). Injects the bearer token and, on a 401, clears the (now-invalid) token and
// notifies the app to re-resolve auth (→ falls back to Login).
export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(input, { ...init, headers: { ...(init.headers ?? {}), ...authHeaders() } });
  if (res.status === 401) {
    clearToken();
    onUnauthorized?.();
  }
  return res;
}
