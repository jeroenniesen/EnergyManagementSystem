import { expect, test } from "@playwright/test";

// #364 SYS-01 — mode blockade recovery is one Dutch sentence; config tokens sit behind Meer.
// Diagnostics are mocked; this never calls a live install.

test("mode blockade recovery is Dutch and hides config tokens behind Meer", async ({ page }) => {
  await page.route("**/api/diagnostics", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        overall: "warn",
        checks: [
          {
            key: "mode",
            label: "Run mode",
            status: "warn",
            detail: "live, dry-run on — config forces watch-only",
          },
        ],
        readiness: {
          control_ready: false,
          sensing_ready: true,
          summary: "Watching only.",
        },
      }),
    });
  });

  await page.goto("/");
  await page.getByTestId("nav-manage").click();
  await page.getByTestId("manage-tab-system").click();

  const sentence = page.getByTestId("recovery-mode-sentence");
  await expect(sentence).toBeVisible();
  await expect(sentence).toContainText("EMS stuurt");
  await expect(sentence).toContainText("Alleen kijken");
  await expect(sentence).toContainText("herstart");
  for (const token of ["config.yaml", "dev.mode", "control.dry_run"]) {
    await expect(sentence).not.toContainText(token);
  }
  await expect(page.getByTestId("recovery-mode-more-body")).toHaveCount(0);

  await page.getByTestId("recovery-mode-more").click();
  const more = page.getByTestId("recovery-mode-more-body");
  await expect(more).toBeVisible();
  for (const token of ["config.yaml", "dev.mode", "control.dry_run"]) {
    await expect(more).toContainText(token);
    await expect(sentence).not.toContainText(token);
  }

  await page.getByTestId("recovery-mode-more").click();
  await expect(page.getByTestId("recovery-mode-more-body")).toHaveCount(0);
});
