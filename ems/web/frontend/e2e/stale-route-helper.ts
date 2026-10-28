import type { Route } from "@playwright/test";

/**
 * Page navigation and test teardown can dispose a `route.fetch()` body, or close
 * the page, while a handler is still between fetch and fulfill. Those failures
 * are races, not bad dashboard data. Anything else (JSON parse, network, bugs)
 * must still fail the test so badge assertions are not the only signal.
 *
 * Matched against the first line only. Playwright appends a call log after it,
 * and prefixes the API name (`apiResponse.json: Response has been disposed`).
 */
const STALE_ROUTE_LINE =
  /response has been disposed|request context disposed|target closed|has been closed|route is already handled|test ended\.|test timeout of \d+ms exceeded|frame (?:was|has been) detached|net::err_aborted|(?:^|:\s*)aborted$/i;

export function isStaleRouteError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  const firstLine = message.split("\n", 1)[0] ?? "";
  if (STALE_ROUTE_LINE.test(firstLine)) return true;
  return error instanceof Error && /TargetClosed/i.test(error.name);
}

/**
 * Finish a route Playwright will not let us fulfill. Abort first so a dead
 * poll cannot overwrite the page with an unpatched dashboard. Fallback only
 * if abort cannot settle it. Both calls are swallowed: the page or context
 * may already be gone, and teardown must not replace an assertion error.
 */
export async function settleAbandonedRoute(route: Route): Promise<void> {
  try {
    await route.abort("aborted");
  } catch {
    try {
      await route.fallback();
    } catch {
      // Already handled, or the page/context is gone.
    }
  }
}

export async function guardRoute(route: Route, handle: () => Promise<void>): Promise<void> {
  try {
    await handle();
  } catch (error) {
    if (!isStaleRouteError(error)) throw error;
    await settleAbandonedRoute(route);
  }
}
