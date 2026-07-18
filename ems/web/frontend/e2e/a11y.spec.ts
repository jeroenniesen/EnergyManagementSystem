import { expect, type Page, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Accessibility gate (BACKLOG B-82, E-09): run axe-core against every major page surface.
// This is a CI gate, not an exhaustive audit — it catches regressions on the main views.
// Individual component a11y is covered by the existing per-page e2e tests.

async function checkA11y(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
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
    await page.waitForSelector("[data-testid='score-card']");
    await checkA11y(page);
  });

  test("Manage/Settings page is accessible", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.waitForSelector("[data-testid='settings-content']");
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
    await page.waitForSelector("[data-testid='readiness-check']");
    await checkA11y(page);
  });

  test("Car page is accessible", async ({ page }) => {
    await page.goto("/");
    const carNav = page.getByTestId("nav-car");
    if (await carNav.isVisible().catch(() => false)) {
      await carNav.click();
      await page.waitForSelector("[data-testid='car-plan']");
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
    await page.goto("/");
    // Set light theme via settings if available.
    await page.getByTestId("nav-manage").click();
    const lightTheme = page.getByRole("radio", { name: /light/i });
    if (await lightTheme.isVisible().catch(() => false)) {
      await lightTheme.click();
    }
    await page.goto("/");
    await page.waitForSelector("[data-testid='hero-verdict']");
    await checkA11y(page);
  });

  test("Theme switch respects contrast (dark)", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    const darkTheme = page.getByRole("radio", { name: /dark/i });
    if (await darkTheme.isVisible().catch(() => false)) {
      await darkTheme.click();
    }
    await page.goto("/");
    await page.waitForSelector("[data-testid='hero-verdict']");
    await checkA11y(page);
  });
});
