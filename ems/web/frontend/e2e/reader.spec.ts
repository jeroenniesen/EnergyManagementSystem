import { expect, type Page, test } from "@playwright/test";

// Reader read-only UI (auth slice 2 web, design §7: "OPERATE controls hidden/disabled for
// readers, mirrors the API 403"). Mocks GET /api/auth to report a reader principal — every other
// endpoint still hits the real backend (authenticated as the e2e admin via the "app" project's
// extraHTTPHeaders), so this is a pure frontend-gating check: does the UI actually hide/disable
// every OPERATE control when told the signed-in role is "reader", independent of what the
// backend would separately enforce.

function mockReaderAuth(page: Page) {
  return page.route("**/api/auth", async (route) => {
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
}

test.describe("reader read-only mode", () => {
  test("OPERATE controls are hidden or disabled for a reader principal", async ({ page }) => {
    await mockReaderAuth(page);
    // AI/chat is off by default on the e2e backend (ui.spec.ts) — mock it on so the input+form
    // actually render, which is what needs to be proven disabled below.
    await page.route("**/api/explainer", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ mode: "template", active: true, language: "English" }),
      }),
    );
    // Same reasoning for the AI second-opinion card below: it only renders once AI is active
    // (AiValidationCard.tsx: `if (!active && !latest) return null`).
    await page.route("**/api/ai/validation", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          latest: { text: "The plan looks sound.", ts: "2026-06-28T18:00:00+00:00", source: "external_llm" },
          active: true,
        }),
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

    // AI second opinion (dashboard, inside "All the details"): "Check now" is an OPERATE write
    // (POST /api/ai/validate) — visible (the review itself is still readable) but disabled, with a
    // hint explaining why.
    await page.getByTestId("advanced-toggle").click();
    const aiCheck = page.getByTestId("ai-check");
    await expect(aiCheck).toBeVisible();
    await expect(aiCheck).toBeDisabled();
    await expect(page.getByTestId("ai-check-readonly-hint")).toBeVisible();

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

  test("Insights heating-advice mark-done/undo are disabled for a reader", async ({ page }) => {
    await mockReaderAuth(page);
    // Gas-configured report (HeatingAdvice only renders once report.gas is non-null) — same shape
    // insights.spec.ts uses for its own heating-advice tests.
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          period: "day",
          window_start: "2026-06-28T00:00:00+00:00",
          window_end: "2026-06-29T00:00:00+00:00",
          label: "2026-06-28",
          partial: false,
          flows: {
            date: "2026-06-28", has_data: true, partial: false,
            solar_to_home: 4, solar_to_car: 1, solar_to_battery: 3, solar_to_grid: 2,
            grid_to_home: 1, grid_to_car: 0.5, grid_to_battery: 0.5,
            battery_to_home: 2.5, battery_to_car: 0, battery_to_grid: 0,
            solar_kwh: 10, grid_import_kwh: 2, grid_export_kwh: 2,
            battery_charge_kwh: 3.5, battery_discharge_kwh: 2.5, home_kwh: 7.5, car_kwh: 1.5,
            self_sufficiency_pct: 80, solar_self_consumption_pct: 80, car_guard_leak_kwh: 0,
          },
          scores: [
            { key: "self_consumption", label: "Self-consumption", value: 80, raw: 80, unit: "%",
              explanation: "Kept 80% of your solar on-site; exported 2.0 kWh you couldn't use or store." },
            { key: "co2", label: "CO₂", value: 60, raw: 1.6, unit: "kg",
              explanation: "Avoided 60% of a no-solar home's CO₂ (2 kg vs 4 kg)." },
            { key: "best_price", label: "Best price", value: 75, raw: 0.13, unit: "€/kWh",
              explanation: "Imported at €0.13/kWh vs the period's €0.08–€0.30 range; ≈ €0.30 saved." },
          ],
          gas: { m3: 8, kwh_eq: 78.2, eur: 12, co2_kg: 14.2 },
        }),
      }),
    );
    // Pin heating.done to "nothing marked yet" — like insights.spec.ts's mark-done tests, this
    // keeps the card in its full (not collapsed-done) state regardless of the shared e2e DB's
    // actual value, so the mark-done control is reliably present to assert disabled.
    await page.route("**/api/settings", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ schema: [], values: { "heating.done": JSON.stringify({}) } }),
      }),
    );

    await page.goto("/");
    await page.getByTestId("nav-insights").click();

    const advice = page.getByTestId("heating-advice");
    await expect(advice).toBeVisible();
    const card = page.getByTestId("advice-balancing");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("advice-balancing-mark-done")).toBeDisabled();
    await expect(page.getByTestId("advice-balancing-readonly-hint")).toBeVisible();
  });
});
