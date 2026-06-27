import { expect, test } from "@playwright/test";

test.describe("EMS dashboard", () => {
  test("renders the status dashboard with reconstructed load", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Smart Energy Manager" })).toBeVisible();

    // DRY-RUN badge must be visible (M0a is read-only).
    await expect(page.getByTestId("run-mode-badge")).toHaveText("DRY-RUN");

    // The status grid renders, including the reconstructed house-load value (1.00 kW).
    const grid = page.getByTestId("status-grid");
    await expect(grid).toBeVisible();
    await expect(grid).toContainText("House load");
    await expect(grid).toContainText("1.00 kW");
    await expect(grid).toContainText("55 %");
  });

  test("no API error banner when backend is up", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("status-grid")).toBeVisible();
    await expect(page.getByTestId("error")).toHaveCount(0);
  });
});
