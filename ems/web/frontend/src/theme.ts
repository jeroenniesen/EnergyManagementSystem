export type Theme = "auto" | "light" | "dark";

const STORAGE_KEY = "ui.theme";

/** Last-known theme cached in localStorage, so the very first paint matches the saved choice
 *  (no flash) before the async /api/settings fetch resolves. Safe if storage is unavailable. */
export function readStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" || v === "auto" ? v : "auto";
  } catch {
    return "auto";
  }
}

export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* private mode / storage disabled — the in-memory state still drives the theme */
  }
}

/** Resolve "auto" against the OS preference; pass-through for explicit choices. */
export function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme === "auto") {
    const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    return prefersDark ? "dark" : "light";
  }
  return theme;
}

/** Set `data-theme` on <html> for the given setting. For "auto", also track OS changes live.
 *  Returns a cleanup that detaches the listener (call on unmount / before re-applying). */
export function applyTheme(theme: Theme): () => void {
  const set = () => {
    document.documentElement.dataset.theme = resolveTheme(theme);
  };
  set();
  if (theme === "auto" && window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", set);
    return () => mq.removeEventListener("change", set);
  }
  return () => {};
}
