// The Manage view + hash routing (feat/ux-batch-3): Settings / System / Audit fold into one
// top-level "Manage" item with a segmented sub-nav. Covers the canonical hashes (#manage,
// #manage/system, #manage/audit), the LEGACY redirects (#settings/#system/#audit — old bookmarks
// and deep-links must keep working), the unknown-hash fallback, sub-tab switching, and that the
// Settings section deep-link mechanics survive when Settings is mounted inside Manage.
import { expect, test, type Page } from "@playwright/test";

const CORE_FAIL = {
  status: 500,
  contentType: "application/json",
  body: '{"detail":"down"}',
};

async function mockCoreReachability(page: Page, reachable: { value: boolean }) {
  const handler = async (route: import("@playwright/test").Route) => {
    if (reachable.value) {
      const response = await route.fetch();
      await route.fulfill({ response });
      return;
    }
    await route.fulfill(CORE_FAIL);
  };
  await page.route("**/api/dashboard", handler);
  await page.route("**/api/status", handler);
}

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
    await expect(page.getByTestId("plan-story")).toBeVisible();
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

  // Mirrors StrategyCard's segmented-control keyboard test: arrow keys move focus AND select,
  // wrapping at the ends, like a native tablist.
  test("the sub-nav is keyboard-operable: arrow keys move focus and select", async ({ page }) => {
    await page.goto("/#manage");
    const settingsTab = page.getByTestId("manage-tab-settings");
    const systemTab = page.getByTestId("manage-tab-system");
    const auditTab = page.getByTestId("manage-tab-audit");

    await expect(settingsTab).toHaveAttribute("tabindex", "0");
    await expect(systemTab).toHaveAttribute("tabindex", "-1");

    await settingsTab.focus();
    await settingsTab.press("ArrowRight");
    await expect(systemTab).toHaveAttribute("aria-selected", "true");
    await expect(systemTab).toBeFocused();
    await expect(page.getByTestId("system")).toBeVisible();

    await systemTab.press("ArrowRight");
    await expect(auditTab).toHaveAttribute("aria-selected", "true");
    await expect(auditTab).toBeFocused();
    await expect(page.getByTestId("audit")).toBeVisible();

    // Wraps from the last tab back to the first.
    await auditTab.press("ArrowRight");
    await expect(settingsTab).toHaveAttribute("aria-selected", "true");
    await expect(settingsTab).toBeFocused();
    await expect(page.getByTestId("settings")).toBeVisible();

    // ArrowLeft moves the other way.
    await settingsTab.press("ArrowLeft");
    await expect(auditTab).toHaveAttribute("aria-selected", "true");
    await expect(auditTab).toBeFocused();
  });

  test("each sub-tab exposes ARIA tab/tabpanel wiring", async ({ page }) => {
    await page.goto("/#manage");
    const settingsTab = page.getByTestId("manage-tab-settings");
    await expect(settingsTab).toHaveAttribute("role", "tab");
    await expect(settingsTab).toHaveAttribute("id", "manage-tab-settings");
    await expect(settingsTab).toHaveAttribute("aria-controls", "manage-panel-settings");

    const panel = page.getByTestId("manage-panel");
    await expect(panel).toHaveAttribute("role", "tabpanel");
    await expect(panel).toHaveAttribute("id", "manage-panel-settings");
    await expect(panel).toHaveAttribute("aria-labelledby", "manage-tab-settings");

    await page.getByTestId("manage-tab-system").click();
    await expect(panel).toHaveAttribute("id", "manage-panel-system");
    await expect(panel).toHaveAttribute("aria-labelledby", "manage-tab-system");
  });
});

test.describe("EMS unreachable banner outside dashboard (#369 NAV-03)", () => {
  test("shows on Manage and Insights while core APIs fail, then clears on recovery", async ({
    page,
  }) => {
    const reachable = { value: false };
    await mockCoreReachability(page, reachable);

    await page.goto("/#manage");
    const banner = page.getByTestId("alert-ems_unreachable");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("niet bereikbaar");

    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("insights")).toBeVisible();
    await expect(page.getByTestId("alert-ems_unreachable")).toBeVisible();

    reachable.value = true;
    await page.getByTestId("nav-manage").click();
    await expect(page.getByTestId("alert-ems_unreachable")).toHaveCount(0);
  });

  test("shows on Trading and Car while core APIs fail", async ({ page }) => {
    await mockCoreReachability(page, { value: false });
    await page.goto("/#trading");
    await expect(page.getByTestId("trading-view")).toBeVisible();
    await expect(page.getByTestId("alert-ems_unreachable")).toBeVisible();

    await page.getByTestId("nav-car").click();
    await expect(page.getByTestId("alert-ems_unreachable")).toBeVisible();
  });
});

test.describe("Audit load error (#369 AUD-01)", () => {
  test("keeps the filter visible with retry when /api/audit fails", async ({ page }) => {
    let auditCalls = 0;
    await page.route("**/api/audit**", (route) => {
      auditCalls += 1;
      if (auditCalls < 3) {
        return route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"down"}' });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          entries: [{ id: 1, ts: "2026-06-01T12:00:00Z", category: "config_change", summary: "OK", detail: {} }],
        }),
      });
    });
    await page.goto("/#manage/audit");
    const filter = page.getByTestId("audit-filter");
    await expect(filter).toBeVisible();
    await expect(page.getByTestId("audit-error")).toBeVisible();
    await filter.selectOption("battery_decision");
    await expect(filter).toHaveValue("battery_decision");
    await expect(page.getByTestId("audit-error")).toBeVisible();
    await page.getByTestId("audit-retry").click();
    await expect(page.getByTestId("audit-list")).toContainText("OK");
    await expect(page.getByTestId("audit-error")).toHaveCount(0);
  });
});

test.describe("Settings section hash (#368)", () => {
  test("opening Control & safety sets #manage/settings/control and survives reload", async ({
    page,
  }) => {
    await page.goto("/#manage");
    await page.getByTestId("group-control").click();
    await expect(page).toHaveURL(/#manage\/settings\/control$/);
    await expect(page.getByRole("heading", { name: "Control & safety" })).toBeVisible();
    await page.reload();
    await expect(page).toHaveURL(/#manage\/settings\/control$/);
    await expect(page.getByRole("heading", { name: "Control & safety" })).toBeVisible();
    await expect(page.getByTestId("group-control")).toHaveAttribute("aria-current", "page");
  });

  test("a copied #manage/settings/ev URL opens the Car section", async ({ page }) => {
    await page.goto("/#manage/settings/ev");
    await expect(page.getByTestId("manage-tab-settings")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("group-ev")).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("heading", { name: "Car" })).toBeVisible();
    await expect(page.getByTestId("field-ev.advice_enabled")).toBeVisible();
  });

  test("switching sections pushes history so browser back shows the previous section", async ({
    page,
  }) => {
    await page.goto("/#manage");
    await page.getByTestId("group-control").click();
    await expect(page).toHaveURL(/#manage\/settings\/control$/);
    await page.getByTestId("group-planner").click();
    await expect(page).toHaveURL(/#manage\/settings\/planner$/);
    await expect(page.getByRole("heading", { name: "Planner economics" })).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/#manage\/settings\/control$/);
    await expect(page.getByRole("heading", { name: "Control & safety" })).toBeVisible();
    await expect(page.getByTestId("group-control")).toHaveAttribute("aria-current", "page");
  });

  test("Inrichten, Car and System deep links open the section and set the hash", async ({ page }) => {
    await page.goto("/#setup");
    await page.getByTestId("setup-btn-battery").click();
    await expect(page).toHaveURL(/#manage\/settings\/battery$/);
    await expect(page.getByTestId("group-battery")).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("heading", { name: "Battery (Indevolt)" })).toBeVisible();

    await page.goto("/#car");
    await page.getByTestId("car-config-settings-link").click();
    await expect(page).toHaveURL(/#manage\/settings\/ev$/);
    await expect(page.getByTestId("group-ev")).toHaveAttribute("aria-current", "page");

    await page.route("**/api/accuracy**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          solar: { bias_w: -12, n_slots: 10 },
          load: { mape_pct: 8, n_hours: 10 },
          plan_execution: { hit_rate_pct: 90, n_deadlines: 4 },
          health: {
            solar: "warn",
            load: "ok",
            plan_execution: "ok",
            notes: ["Solar forecast bias is beyond 25% of typical output over the last 14 days."],
          },
        }),
      }),
    );
    await page.goto("/#manage/system");
    await page.getByTestId("health-action-link-solar").click();
    await expect(page).toHaveURL(/#manage\/settings\/planner$/);
    await expect(page.getByTestId("group-planner")).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("field-planner.solar_confidence")).toBeVisible();
  });

  test("mobile browser back from a section shows the section list", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/#manage");
    await expect(page.getByTestId("settings")).toHaveAttribute("data-mobile", "list");
    await expect(page.getByTestId("settings-back")).toBeHidden();
    await page.getByTestId("group-control").click();
    await expect(page).toHaveURL(/#manage\/settings\/control$/);
    await expect(page.getByTestId("settings")).toHaveAttribute("data-mobile", "section");
    await expect(page.getByTestId("settings-back")).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/#manage$/);
    await expect(page.getByTestId("settings")).toHaveAttribute("data-mobile", "list");
    await expect(page.getByTestId("group-control")).toBeVisible();
    await expect(page.getByTestId("settings-back")).toBeHidden();
    await expect(page.getByRole("heading", { name: "Control & safety" })).toBeHidden();
  });

  test("an unknown section id opens the first section; manage routes stay put", async ({ page }) => {
    await page.goto("/#manage/settings/not-a-real-section");
    await expect(page).toHaveURL(/#manage\/settings\/not-a-real-section$/);
    await expect(page.getByTestId("settings-error")).toHaveCount(0);
    await expect(page.getByTestId("group-connection")).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("heading", { name: "Connection" })).toBeVisible();

    await page.goto("/#manage");
    await expect(page).toHaveURL(/#manage$/);
    await expect(page.getByTestId("manage-tab-settings")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("settings")).toBeVisible();

    await page.goto("/#manage/system");
    await expect(page).toHaveURL(/#manage\/system$/);
    await expect(page.getByTestId("manage-tab-system")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("system")).toBeVisible();

    await page.goto("/#manage/audit");
    await expect(page).toHaveURL(/#manage\/audit$/);
    await expect(page.getByTestId("manage-tab-audit")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("audit")).toBeVisible();
  });
});
