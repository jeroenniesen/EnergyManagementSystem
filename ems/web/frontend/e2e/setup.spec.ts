import { expect, type Page, test } from "@playwright/test";

const CHECKLIST_BASE = {
  headline: "Nog 1 stap tot EMS kan meekijken",
  steps_remaining: 1,
  watch_ready: false,
  readiness: { control_ready: false, sensing_ready: false },
};

function batteryItem(status: "Werkt" | "Ontbreekt") {
  return {
    key: "battery",
    sentence: "EMS ziet je batterij en weet hoe vol ze is.",
    status,
    button_label: "Batterij instellen",
    settings_section: "battery",
  };
}

function mockAuthMe(page: Page) {
  return page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        username: "e2e-admin",
        role: "admin",
        kind: "session",
        restart_available: { available: true, reason: "ok" },
        restart_pending: false,
      }),
    });
  });
}

function mockSettings(page: Page) {
  return page.route("**/api/settings", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schema: [],
        values: { "meters.p1_ip": "192.168.1.10" },
      }),
    });
  });
}

test.describe("Setup wizard battery (#291)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as { __EMS_RESTART_POLL__: { intervalMs: number; timeoutMs: number } })
        .__EMS_RESTART_POLL__ = { intervalMs: 80, timeoutMs: 3000 };
    });
  });

  test("unreachable IP: one restart, error with IP, retry visible", async ({ page }) => {
    await mockAuthMe(page);
    await mockSettings(page);
    let checklistStatus: "Werkt" | "Ontbreekt" = "Ontbreekt";
    await page.route("**/api/setup/checklist", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...CHECKLIST_BASE,
          items: [batteryItem(checklistStatus)],
        }),
      });
    });
    await page.route("**/api/setup/settings", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ values: {}, restart_required: true }),
      });
    });
    let restartPosts = 0;
    await page.route("**/api/system/restart", async (route) => {
      restartPosts += 1;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ restarting: true, boot_id: "boot-a" }),
      });
    });
    let healthCalls = 0;
    await page.route("**/health/live", async (route) => {
      healthCalls += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "alive",
          boot_id: healthCalls >= 2 ? "boot-b" : "boot-a",
        }),
      });
    });

    await page.goto("/#setup");
    await page.getByTestId("setup-btn-battery").click();
    await page.getByTestId("setup-battery-ip").fill("192.168.99.50");
    const loadPromise = page.waitForEvent("load");
    await page.getByTestId("setup-battery-save").click();
    await expect(page.getByTestId("setup-battery-restarting")).toBeVisible();
    await loadPromise;

    await expect(page.getByTestId("setup-battery-wizard")).toBeVisible();
    await expect(page.getByTestId("setup-item-battery")).toContainText("Ontbreekt");
    await expect(page.getByTestId("setup-battery-error")).toContainText("192.168.99.50");
    await expect(page.getByTestId("setup-battery-retry")).toBeVisible();
    expect(restartPosts).toBe(1);
    await page.waitForTimeout(500);
    expect(restartPosts).toBe(1);
  });

  test("happy path: save, restart text, reload, battery Werkt", async ({ page }) => {
    await mockAuthMe(page);
    await mockSettings(page);
    let checklistStatus: "Werkt" | "Ontbreekt" = "Ontbreekt";
    await page.route("**/api/setup/checklist", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...CHECKLIST_BASE,
          headline: checklistStatus === "Werkt" ? "EMS kan meekijken" : CHECKLIST_BASE.headline,
          items: [batteryItem(checklistStatus)],
        }),
      });
    });
    await page.route("**/api/setup/settings", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ values: {}, restart_required: true }),
      });
    });
    await page.route("**/api/system/restart", async (route) => {
      checklistStatus = "Werkt";
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ restarting: true, boot_id: "boot-x" }),
      });
    });
    let healthCalls = 0;
    await page.route("**/health/live", async (route) => {
      healthCalls += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "alive",
          boot_id: healthCalls >= 2 ? "boot-y" : "boot-x",
        }),
      });
    });

    await page.goto("/#setup");
    await page.getByTestId("setup-btn-battery").click();
    await page.getByTestId("setup-battery-ip").fill("192.168.1.60");
    const loadPromise = page.waitForEvent("load");
    await page.getByTestId("setup-battery-save").click();
    await expect(page.getByTestId("setup-battery-restarting")).toBeVisible();
    await loadPromise;

    await expect(page.getByTestId("setup-battery-success")).toBeVisible();
    await expect(page.getByTestId("setup-item-battery")).toContainText("Werkt");
  });
});
