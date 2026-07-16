import { expect, test } from "@playwright/test";

// Contextual dashboard drawer — mobile, accessibility, and visual polish (2026-07-15 plan Task 6).
// Presentation is CSS-only (no viewport branching in JS); behaviour (focus trap, Escape, focus
// restoration) lives in the DetailDrawer component.

test.describe("dashboard drawer", () => {
  test("is right-aligned on desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/#dashboard/now");
    const panel = page.getByTestId("detail-drawer");
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    // The panel hugs the right edge: it starts past the halfway line and ends at the viewport edge.
    expect(box!.x).toBeGreaterThan(640);
    expect(box!.x + box!.width).toBeGreaterThan(1280 - 4);
  });

  test("is a full-width sheet on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/#dashboard/now");
    const panel = page.getByTestId("detail-drawer");
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box!.width).toBeGreaterThan(390 - 4); // effectively full width
  });

  test("traps focus within the drawer while tabbing", async ({ page }) => {
    await page.goto("/#dashboard/now");
    await expect(page.getByTestId("detail-drawer")).toBeVisible();
    await expect(page.getByTestId("detail-drawer-close")).toBeFocused();
    // Tabbing repeatedly must never move focus outside the dialog.
    for (let i = 0; i < 6; i++) await page.keyboard.press("Tab");
    const inside = await page.evaluate(() => {
      const panel = document.querySelector('[data-testid="detail-drawer"]');
      return !!panel && !!document.activeElement && panel.contains(document.activeElement);
    });
    expect(inside).toBe(true);
    // Shift+Tab from the first control also stays inside (wraps to the last).
    await page.getByTestId("detail-drawer-close").focus();
    await page.keyboard.press("Shift+Tab");
    const stillInside = await page.evaluate(() => {
      const panel = document.querySelector('[data-testid="detail-drawer"]');
      return !!panel && !!document.activeElement && panel.contains(document.activeElement);
    });
    expect(stillInside).toBe(true);
  });

  test("Escape closes and returns focus to the opening trigger", async ({ page }) => {
    await page.goto("/");
    const trigger = page.getByTestId("dashboard-now-trigger");
    await trigger.click();
    await expect(page.getByTestId("detail-drawer")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("detail-drawer")).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });

  test("the dialog is labelled and modal", async ({ page }) => {
    await page.goto("/#dashboard/now");
    const panel = page.getByTestId("detail-drawer");
    await expect(panel).toHaveAttribute("role", "dialog");
    await expect(panel).toHaveAttribute("aria-modal", "true");
    await expect(panel).toHaveAttribute("aria-labelledby", "detail-drawer-title");
    await expect(page.getByTestId("detail-drawer-heading")).toBeVisible();
  });

  test("respects reduced motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/#dashboard/now");
    const panel = page.getByTestId("detail-drawer");
    await expect(panel).toBeVisible();
    const anim = await panel.evaluate((el) => getComputedStyle(el).animationName);
    expect(anim === "none" || anim === "").toBe(true);
  });

  test("the dashboard behind the drawer keeps its content mounted (scroll preserved)", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByTestId("home-state")).toBeVisible();
    await page.getByTestId("dashboard-now-trigger").click();
    await expect(page.getByTestId("detail-drawer")).toBeVisible();
    // The dashboard is not unmounted/replaced — its hero is still in the DOM under the drawer.
    await expect(page.getByTestId("home-state")).toBeAttached();
  });
});
