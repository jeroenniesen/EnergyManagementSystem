import { expect, type Page, test } from "@playwright/test";

// Apply & restart UX (I3, spec §B.4). The capability + pending flag are server-computed on
// GET /api/auth/me (I2) — mocked here so every scenario is deterministic and never depends on the
// shared e2e DB's actual boot fingerprint or on EMS_SUPERVISED/session-vs-access-token state (the
// "app" project authenticates via an ACCESS token, so the REAL /api/auth/me would always report
// `available:false` — kind !== "session").

const SCHEMA = [
  {
    key: "meters.p1_ip", label: "P1 meter IP", type: "text", default: "",
    group: "meters", help: "", min: null, max: null, options: null, step: null, unit: "",
    advanced: false, applies: "restart",
  },
];
const BASE_VALUES: Record<string, string> = { "meters.p1_ip": "192.168.1.10" };

// `pending` may be a single value (every call answers the same) or a sequence (each call advances
// one step, clamped at the last entry) — the first test uses a sequence to prove the button is
// specifically a RESULT of the post-save capability refetch, not just present from page load.
function mockAuthMe(
  page: Page,
  opts: { available: boolean; reason: string; pending: boolean | boolean[] },
) {
  let call = 0;
  return page.route("**/api/auth/me", async (route) => {
    const pending = Array.isArray(opts.pending)
      ? opts.pending[Math.min(call, opts.pending.length - 1)]
      : opts.pending;
    call += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        username: "e2e-admin", role: "admin", kind: "session",
        restart_available: { available: opts.available, reason: opts.reason },
        restart_pending: pending,
      }),
    });
  });
}

function mockSettingsSave(page: Page) {
  return page.route("**/api/settings", async (route) => {
    if (route.request().method() === "POST") {
      const saved = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ values: { ...BASE_VALUES, ...saved } }),
      });
    } else {
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ schema: SCHEMA, values: BASE_VALUES }),
      });
    }
  });
}

test.describe("Apply & restart", () => {
  test("saving a restart-tagged setting surfaces the button; confirm -> 202 -> boot_id "
    + "change -> reload", async ({ page }) => {
    await mockAuthMe(page, { available: true, reason: "ok", pending: [false, true] });
    await mockSettingsSave(page);

    let restartPosted = false;
    await page.route("**/api/system/restart", async (route) => {
      restartPosted = true;
      await route.fulfill({
        status: 202, contentType: "application/json",
        body: JSON.stringify({ restarting: true, boot_id: "B" }),
      });
    });
    let healthCalls = 0;
    await page.route("**/health/live", async (route) => {
      healthCalls += 1;
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ status: "alive", boot_id: healthCalls < 2 ? "B" : "C" }),
      });
    });

    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-meters").click();

    // Not pending yet (first /api/auth/me answer) — no button.
    await expect(page.getByTestId("restart-apply-button")).toHaveCount(0);

    await page.locator("#set-meters\\.p1_ip").fill("192.168.1.20");
    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();

    // The save's post-success refetch now reports restart_pending:true -> the button appears.
    const applyBtn = page.getByTestId("restart-apply-button");
    await expect(applyBtn).toBeVisible();
    await expect(applyBtn).toHaveText("Apply & restart");

    await applyBtn.click();
    const confirmPanel = page.getByTestId("restart-confirm-panel");
    await expect(confirmPanel).toBeVisible();
    await expect(confirmPanel).toContainText("safe AUTO mode");
    await expect(confirmPanel).toContainText("try again in a moment");
    await expect(confirmPanel).toContainText("comes back on its own");

    const loadPromise = page.waitForEvent("load");
    await page.getByTestId("restart-confirm").click();
    await expect(page.getByTestId("restart-restarting")).toBeVisible();
    await loadPromise; // proves the boot_id-changed poll actually triggered window.location.reload()
    expect(restartPosted).toBe(true);
  });

  test("unsupervised: manual-restart hint, no button", async ({ page }) => {
    await mockAuthMe(page, { available: false, reason: "not_supervised", pending: true });
    await mockSettingsSave(page);

    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-meters").click();

    const hint = page.getByTestId("restart-manual-hint");
    await expect(hint).toBeVisible();
    await expect(hint).toContainText("scripts/restart.sh");
    await expect(page.getByTestId("restart-apply-button")).toHaveCount(0);
    await expect(page.getByTestId("restart-confirm-panel")).toHaveCount(0);
  });

  test("409 busy: retry toast, button stays", async ({ page }) => {
    await mockAuthMe(page, { available: true, reason: "ok", pending: true });
    await mockSettingsSave(page);
    await page.route("**/api/system/restart", async (route) => {
      await route.fulfill({
        status: 409, contentType: "application/json",
        body: JSON.stringify({
          detail: "controller is mid-operation — try again in a few seconds",
          reason: "writer_registry_busy",
        }),
      });
    });

    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-meters").click();

    const applyBtn = page.getByTestId("restart-apply-button");
    await expect(applyBtn).toBeVisible();
    await applyBtn.click();
    await page.getByTestId("restart-confirm").click();

    const toast = page.getByTestId("restart-busy-toast");
    await expect(toast).toBeVisible();
    await expect(toast).toContainText("try again in a few seconds");
    // The button stays available for another try (the confirm panel closes back to it, not stuck).
    await expect(page.getByTestId("restart-apply-button")).toBeVisible();
    await expect(page.getByTestId("restart-confirm-panel")).toHaveCount(0);
  });
});
