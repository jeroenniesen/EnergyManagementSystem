import { expect, test } from "@playwright/test";

// Reader read-only UI (auth slice 2 web, design §7: "OPERATE controls hidden/disabled for
// readers, mirrors the API 403"). Mocks GET /api/auth to report a reader principal — every other
// endpoint still hits the real backend (authenticated as the e2e admin via the "app" project's
// extraHTTPHeaders), so this is a pure frontend-gating check: does the UI actually hide/disable
// every OPERATE control when told the signed-in role is "reader", independent of what the
// backend would separately enforce.

test.describe("reader read-only mode", () => {
  test("OPERATE controls are hidden or disabled for a reader principal", async ({ page }) => {
    await page.route("**/api/auth", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          required: true,
          authenticated: true,
          onboarding_needed: false,
          user: { username: "reader-e2e", role: "reader" },
          shared_token_required: false,
        }),
      });
    });
    // AI/chat is off by default on the e2e backend (ui.spec.ts) — mock it on so the input+form
    // actually render, which is what needs to be proven disabled below.
    await page.route("**/api/explainer", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ mode: "template", active: true, language: "English" }),
      }),
    );

    await page.goto("/");
    await page.getByTestId("home-more-toggle").click();

    // Override: no mutating controls — a read-only hint stands in for them (the head/badge, a
    // plain read, stays visible).
    await expect(page.getByTestId("override")).toBeVisible();
    await expect(page.getByTestId("override-intent")).toHaveCount(0);
    await expect(page.getByTestId("override-apply")).toHaveCount(0);
    await expect(page.getByTestId("override-readonly-hint")).toBeVisible();

    // Strategy: season switch + grid-topup toggle are disabled (not removed — the current choice
    // stays visible, it just can't be changed).
    const summerBtn = page.getByTestId("strategy-summer");
    await expect(summerBtn).toBeVisible();
    await expect(summerBtn).toBeDisabled();

    // Settings: fields disabled, no save bar (a reader can never dirty a field to begin with), and
    // the admin-only Access & security panel is absent.
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-ui").click();
    await expect(page.locator("#set-ui\\.theme")).toBeDisabled();
    await expect(page.getByTestId("settings-savebar")).toHaveCount(0);
    await expect(page.getByTestId("admin-users")).toHaveCount(0);
    await expect(page.getByTestId("admin-invites")).toHaveCount(0);

    // Chat: input + send disabled, with a hint explaining why (the FAQ quick-answers still work).
    await page.getByTestId("nav-chat").click();
    await expect(page.getByTestId("chat-input")).toBeDisabled();
  });
});
