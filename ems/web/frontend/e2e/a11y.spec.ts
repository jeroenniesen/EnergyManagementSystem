import { expect, type Page, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Accessibility gate (BACKLOG B-82, E-09): run axe-core against every major page surface.
// This is a CI gate, not an exhaustive audit — it catches regressions on the main views.
// Individual component a11y is covered by the existing per-page e2e tests.

async function checkA11y(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
}

// Minimal stand-in schema for the theme tests below — mirrors theme.spec.ts's mocked-API
// approach so switching the theme never writes to the shared e2e DB (which every spec file in
// the "app" project shares for the whole run).
const THEME_SCHEMA = [
  {
    key: "ui.theme", label: "Theme", type: "enum", default: "auto",
    group: "ui", help: "", min: null, max: null,
    options: ["auto", "dark", "light"], step: null, unit: "",
  },
];

/** Drives the REAL theme switch (Manage -> ui.theme select -> Save) against a mocked
 *  /api/settings, then asserts <html data-theme> actually flipped before returning — so the
 *  caller can never go on to axe-scan the wrong theme. */
async function switchTheme(page: Page, theme: "light" | "dark") {
  let current = "auto";
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "POST") {
      const body = JSON.parse(route.request().postData() || "{}");
      if (typeof body["ui.theme"] === "string") current = body["ui.theme"];
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ values: { "ui.theme": current } }),
      });
    } else {
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ schema: THEME_SCHEMA, values: { "ui.theme": current } }),
      });
    }
  });
  await page.goto("/");
  await page.getByTestId("nav-manage").click();
  await page.getByTestId("group-ui").click();
  await page.locator("#set-ui\\.theme").selectOption(theme);
  await page.getByTestId("settings-save").click();
  await expect(page.getByTestId("settings-saved")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

test.describe("WCAG 2.1 AA accessibility gate", () => {
  test("Dashboard首页 is accessible", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector("[data-testid='hero-verdict']");
    await checkA11y(page);
  });

  test("Insights page is accessible", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await page.waitForSelector("[data-testid='score-grid']");
    await checkA11y(page);
  });

  test("Manage/Settings page is accessible", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.waitForSelector("[data-testid='settings']");
    await checkA11y(page);
  });

  test("Manage/System page is accessible", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    // Switch to System sub-tab.
    const systemTab = page.getByRole("tab", { name: /system/i });
    if (await systemTab.isVisible().catch(() => false)) {
      await systemTab.click();
    }
    await page.waitForSelector("[data-testid='system']");
    await checkA11y(page);
  });

  test("Car page is accessible", async ({ page }) => {
    await page.goto("/");
    const carNav = page.getByTestId("nav-car");
    if (await carNav.isVisible().catch(() => false)) {
      await carNav.click();
      // Not "car-card": that testid only appears once /api/car/plan resolves AND the EV advisor
      // is enabled (ems/web/routes/car.py's `enabled:false` state renders "car-card-disabled"
      // instead) — the a11y DB run has it off by default, so waiting on "car-card" would hang.
      // "car-view" is Car.tsx's outer section, rendered unconditionally as soon as the tab mounts.
      await page.waitForSelector("[data-testid='car-view']");
    }
    await checkA11y(page);
  });

  test("Override page is accessible", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    // The override control lives on the dashboard advanced section or a dedicated view.
    const override = page.getByTestId("override-toggle");
    if (await override.isVisible().catch(() => false)) {
      await override.click();
    }
    await checkA11y(page);
  });

  test("Theme switch respects contrast (light)", async ({ page }) => {
    await switchTheme(page, "light");
    await page.getByTestId("nav-dashboard").click();
    await page.waitForSelector("[data-testid='hero-verdict']");
    await checkA11y(page);
  });

  test("Theme switch respects contrast (dark)", async ({ page }) => {
    await switchTheme(page, "dark");
    await page.getByTestId("nav-dashboard").click();
    await page.waitForSelector("[data-testid='hero-verdict']");
    await checkA11y(page);
  });
});
