// The Manage view + hash routing (feat/ux-batch-3): Settings / System / Audit fold into one
// top-level "Manage" item with a segmented sub-nav. Covers the canonical hashes (#manage,
// #manage/system, #manage/audit), the LEGACY redirects (#settings/#system/#audit — old bookmarks
// and deep-links must keep working), the unknown-hash fallback, sub-tab switching, and that the
// Settings section deep-link mechanics survive when Settings is mounted inside Manage.
import { expect, test } from "@playwright/test";

test.describe("Manage view + hash routing", () => {
  test("legacy #settings redirects to Manage → Settings", async ({ page }) => {
    await page.goto("/#settings");
    await expect(page.getByTestId("nav-manage")).toHaveClass(/nav-active/);
    await expect(page.getByTestId("manage-tab-settings")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("settings")).toBeVisible();
  });

  test("legacy #system redirects to Manage → System", async ({ page }) => {
    await page.goto("/#system");
    await expect(page.getByTestId("nav-manage")).toHaveClass(/nav-active/);
    await expect(page.getByTestId("manage-tab-system")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("system")).toBeVisible();
  });

  test("legacy #audit redirects to Manage → Audit", async ({ page }) => {
    await page.goto("/#audit");
    await expect(page.getByTestId("manage-tab-audit")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("audit")).toBeVisible();
  });

  test("canonical #manage opens the Settings sub-tab by default", async ({ page }) => {
    await page.goto("/#manage");
    await expect(page.getByTestId("manage-tab-settings")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("settings")).toBeVisible();
  });

  test("canonical #manage/system deep-links straight to the System sub-tab", async ({ page }) => {
    await page.goto("/#manage/system");
    await expect(page.getByTestId("manage-tab-system")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("system")).toBeVisible();
  });

  test("an unknown hash falls back to the dashboard", async ({ page }) => {
    await page.goto("/#definitely-not-a-view");
    await expect(page.getByTestId("nav-dashboard")).toHaveClass(/nav-active/);
    await expect(page.getByTestId("battery-plan")).toBeVisible();
  });

  test("the sub-nav switches between Settings, System and Audit", async ({ page }) => {
    await page.goto("/#manage");
    await expect(page.getByTestId("settings")).toBeVisible();

    await page.getByTestId("manage-tab-system").click();
    await expect(page.getByTestId("system")).toBeVisible();
    await expect(page.getByTestId("settings")).toHaveCount(0);

    await page.getByTestId("manage-tab-audit").click();
    await expect(page.getByTestId("audit")).toBeVisible();

    await page.getByTestId("manage-tab-settings").click();
    await expect(page.getByTestId("settings")).toBeVisible();
  });

  test("switching sub-tab updates the hash so it round-trips (back/forward + reload)", async ({
    page,
  }) => {
    await page.goto("/#manage");
    await page.getByTestId("manage-tab-system").click();
    await expect(page).toHaveURL(/#manage\/system$/);
    // A reload at the deep-link lands on the same sub-tab.
    await page.reload();
    await expect(page.getByTestId("system")).toBeVisible();
  });

  test("deep-link to a Settings section still works inside Manage", async ({ page }) => {
    // The Settings two-pane section navigation must survive being mounted inside Manage — clicking
    // a sidebar section opens it in the content pane exactly as before the restructure.
    await page.goto("/#manage");
    await expect(page.getByTestId("settings")).toBeVisible();
    await page.getByTestId("group-planner").click();
    await expect(page.getByTestId("settings")).toContainText("Planner economics");
    // And a real field of that section is rendered (the section actually opened, not just its nav).
    await expect(page.getByTestId("field-planner.solar_confidence")).toBeVisible();
  });
});
