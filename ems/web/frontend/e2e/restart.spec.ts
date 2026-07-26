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

  test("409 single-flight busy: retry toast, button stays (not the manual hint)", async ({ page }) => {
    await mockAuthMe(page, { available: true, reason: "ok", pending: true });
    await mockSettingsSave(page);
    // A single-flight 409 (a restart already in progress) carries the REAL `in_progress` reason the
    // backend returns — the client must treat it as busy (retry toast, button stays), NOT fall back
    // to the unsupervised manual hint. (Previously this mock invented `writer_registry_busy`, a
    // value the backend never emits.)
    await page.route("**/api/system/restart", async (route) => {
      await route.fulfill({
        status: 409, contentType: "application/json",
        body: JSON.stringify({
          detail: "restart already in progress",
          reason: "in_progress",
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
    // A single-flight/busy 409 must NOT be misread as unsupervised.
    await expect(page.getByTestId("restart-manual-hint")).toHaveCount(0);
  });

  // Fix 8: the poll interval/timeout are injectable via `window.__EMS_RESTART_POLL__` so these
  // specs run in well under a second instead of the 60s production budget.
  function injectFastPoll(page: Page, intervalMs: number, timeoutMs: number) {
    return page.addInitScript(
      ([i, t]) => {
        (window as unknown as {
          __EMS_RESTART_POLL__?: { intervalMs: number; timeoutMs: number };
        }).__EMS_RESTART_POLL__ = { intervalMs: i, timeoutMs: t };
      },
      [intervalMs, timeoutMs] as const,
    );
  }

  test("boot_id-sensitive: stays 'Restarting…' while boot_id is unchanged, reloads only when it "
    + "changes", async ({ page }) => {
    await injectFastPoll(page, 40, 10_000);
    await mockAuthMe(page, { available: true, reason: "ok", pending: true });
    await mockSettingsSave(page);
    await page.route("**/api/system/restart", async (route) => {
      await route.fulfill({
        status: 202, contentType: "application/json",
        body: JSON.stringify({ restarting: true, boot_id: "OLD" }),
      });
    });
    // /health/live keeps answering with the SAME boot_id ("OLD" — the old process still up
    // mid-shutdown) until the test flips it. A plain "alive" on the OLD boot_id must NOT reload.
    let flip = false;
    let healthCalls = 0;
    await page.route("**/health/live", async (route) => {
      healthCalls += 1;
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ status: "alive", boot_id: flip ? "NEW" : "OLD" }),
      });
    });

    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-meters").click();

    const applyBtn = page.getByTestId("restart-apply-button");
    await expect(applyBtn).toBeVisible();
    await applyBtn.click();
    await page.getByTestId("restart-confirm").click();
    await expect(page.getByTestId("restart-restarting")).toBeVisible();

    // Several polls happen while boot_id is UNCHANGED — the UI must stay "Restarting…", no reload.
    await expect.poll(() => healthCalls).toBeGreaterThanOrEqual(3);
    await expect(page.getByTestId("restart-restarting")).toBeVisible();

    // Flip to a DIFFERENT boot_id → the next poll proves the NEW process is up and reloads.
    const loadPromise = page.waitForEvent("load");
    flip = true;
    await loadPromise;
  });

  test("timeout fallback: boot_id never changes past the timeout → manual-reload prompt",
    async ({ page }) => {
    await injectFastPoll(page, 40, 250);
    await mockAuthMe(page, { available: true, reason: "ok", pending: true });
    await mockSettingsSave(page);
    await page.route("**/api/system/restart", async (route) => {
      await route.fulfill({
        status: 202, contentType: "application/json",
        body: JSON.stringify({ restarting: true, boot_id: "STUCK" }),
      });
    });
    // The boot_id NEVER changes → the poll can never confirm a new process and must fall back to
    // the manual-reload prompt after the (injected, tiny) timeout.
    await page.route("**/health/live", async (route) => {
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ status: "alive", boot_id: "STUCK" }),
      });
    });

    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-meters").click();

    const applyBtn = page.getByTestId("restart-apply-button");
    await expect(applyBtn).toBeVisible();
    await applyBtn.click();
    await page.getByTestId("restart-confirm").click();
    await expect(page.getByTestId("restart-restarting")).toBeVisible();

    // Past the timeout with no boot_id change → timeout copy + a working "Reload now" button.
    await expect(page.getByTestId("restart-timeout")).toBeVisible();
    const reloadBtn = page.getByTestId("restart-reload-manual");
    await expect(reloadBtn).toBeVisible();
    const loadPromise = page.waitForEvent("load");
    await reloadBtn.click();
    await loadPromise; // "Reload now" triggers a real reload
  });
});
