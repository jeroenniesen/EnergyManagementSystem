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
