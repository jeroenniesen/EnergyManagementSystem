import { expect, test } from "@playwright/test";

// #364 — consequence + explicit confirm before a draft arms live control.
// page.route only: the POST never reaches the hermetic server, let alone a device.

const DRY_RUN = {
  key: "control.dry_run",
  label: "Watch only (no battery writes)",
  type: "bool",
  default: true,
  group: "control",
  help: "",
  min: null,
  max: null,
  options: null,
  step: null,
  unit: "",
  advanced: false,
  applies: "restart",
};
const OPERATIONAL = {
  key: "control.operational",
  label: "Let the system control the battery",
  type: "bool",
  default: false,
  group: "control",
  help: "",
  min: null,
  max: null,
  options: null,
  step: null,
  unit: "",
  advanced: false,
  applies: "restart",
};

function mockControlSettings(
  page: import("@playwright/test").Page,
  values: { dryRun: boolean; operational: boolean },
  posts: { body: Record<string, unknown> | null; count: number },
) {
  const base = {
    "control.dry_run": values.dryRun,
    "control.operational": values.operational,
    "ui.theme": "light",
  };
  return page.route("**/api/settings", async (route) => {
    if (route.request().method() === "POST") {
      posts.count += 1;
      posts.body = JSON.parse(route.request().postData() || "{}") as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ values: { ...base, ...posts.body } }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ schema: [DRY_RUN, OPERATIONAL], values: base }),
    });
  });
}

async function openControl(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByTestId("nav-manage").click();
  await page.getByTestId("group-control").click();
}

test.describe("#364 confirm control in Settings", () => {
  test("Watch only off shows the consequence; Save posts only after confirmation", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const posts = { body: null as Record<string, unknown> | null, count: 0 };
    await mockControlSettings(page, { dryRun: true, operational: false }, posts);

    await openControl(page);

    const hint = page.locator(".settings-section-hint");
    await expect(hint).toBeVisible();
    await expect(hint).not.toContainText(/dry_run/i);
    await expect(hint).not.toContainText(/mock/i);

    const watch = page.locator("#set-control\\.dry_run");
    await expect(watch).toBeChecked();
    await watch.click();
    await expect(watch).not.toBeChecked();

    const consequence = page.getByTestId("control-arm-consequence");
    await expect(consequence).toBeVisible();
    await expect(consequence).toContainText("batterij aansturen");
    await expect(consequence).toContainText("pas actief na een herstart");
    await expect(consequence).toContainText("laadvenster");

    for (const theme of ["light", "dark"] as const) {
      await page.evaluate((next) => {
        document.documentElement.dataset.theme = next;
      }, theme);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(consequence).toBeVisible();
      const box = await consequence.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(390);
    }

    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("control-arm-confirm")).toBeVisible();
    await expect(page.getByTestId("control-arm-confirm-save")).toHaveText("Opslaan en sturen toestaan");
    expect(posts.count).toBe(0);

    await page.getByTestId("control-arm-cancel").click();
    await expect(page.getByTestId("control-arm-confirm")).toHaveCount(0);
    await expect(page.getByTestId("settings-save")).toBeVisible();
    expect(posts.count).toBe(0);

    await page.getByTestId("settings-save").click();
    await page.getByTestId("control-arm-confirm-save").click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    expect(posts.count).toBe(1);
    expect(posts.body).toEqual({ "control.dry_run": false });
    await expect(page.getByTestId("settings-saved")).toContainText("Wacht op herstart");
    await expect(page.getByTestId("apply-control.dry_run")).toHaveText("Wacht op herstart");
    await expect(page.getByTestId("settings")).not.toContainText("restart_pending");
  });

  test("turning Let the system control on uses the same confirm gate", async ({ page }) => {
    const posts = { body: null as Record<string, unknown> | null, count: 0 };
    await mockControlSettings(page, { dryRun: true, operational: false }, posts);
    await openControl(page);

    const operational = page.locator("#set-control\\.operational");
    await expect(operational).not.toBeChecked();
    await operational.click();

    await expect(page.getByTestId("control-arm-consequence")).toBeVisible();
    await page.getByTestId("settings-save").click();
    expect(posts.count).toBe(0);
    await page.getByTestId("control-arm-confirm-save").click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    expect(posts.count).toBe(1);
    expect(posts.body).toEqual({ "control.operational": true });
  });

  test("the safer direction saves on Save without the consequence or extra confirm", async ({ page }) => {
    const posts = { body: null as Record<string, unknown> | null, count: 0 };
    await mockControlSettings(page, { dryRun: false, operational: true }, posts);
    await openControl(page);

    await page.locator("#set-control\\.dry_run").click();
    await expect(page.getByTestId("control-arm-consequence")).toHaveCount(0);
    await page.locator("#set-control\\.operational").click();
    await expect(page.getByTestId("control-arm-consequence")).toHaveCount(0);

    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    await expect(page.getByTestId("control-arm-confirm")).toHaveCount(0);
    expect(posts.count).toBe(1);
    expect(posts.body).toMatchObject({
      "control.dry_run": true,
      "control.operational": false,
    });
  });
});
