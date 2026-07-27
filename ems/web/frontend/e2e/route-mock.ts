import { expect, type Page, type Request, type Route } from "@playwright/test";

type RouteMatcher = string | RegExp | ((url: URL) => boolean);
type RouteHandler = (route: Route, request: Request) => unknown | Promise<unknown>;

export type ExercisedRoute = {
  assertRequested: () => void;
};

export async function mockRoute(
  page: Page,
  matcher: RouteMatcher,
  handler: RouteHandler,
): Promise<ExercisedRoute> {
  let requestCount = 0;
  await page.route(matcher, async (route, request) => {
    requestCount += 1;
    await handler(route, request);
  });
  return {
    assertRequested: () => {
      expect(
        requestCount,
        `Expected mocked route ${String(matcher)} to be requested at least once`,
      ).toBeGreaterThan(0);
    },
  };
}
