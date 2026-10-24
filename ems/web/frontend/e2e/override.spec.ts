import { expect, test } from "@playwright/test";

const OPTIONS = [
  "allow_self_consumption",
  "grid_charge_to_target",
  "hold_reserve",
  "discharge_for_load",
];

test.describe("EMS operator override", () => {
  test("GET /api/override returns state and the intent options", async ({ request }) => {
    const r = await request.get("/api/override");
    expect(r.ok()).toBeTruthy();
    const b = await r.json();
    expect(b.active).toBe(false); // default: following the plan (fresh DB)
    expect(b.options).toEqual(expect.arrayContaining(["hold_reserve", "grid_charge_to_target"]));
  });

  test("operator can apply an override and then clear it", async ({ page }) => {
    // Stateful mock so the apply/clear flow round-trips without touching the shared dev DB.
    let active = false;
    await page.route("**/api/override", async (route) => {
      if (route.request().method() === "POST") {
        const body = JSON.parse(route.request().postData() || "{}");
        active = body.intent != null && body.intent !== "" && body.intent !== "none";
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          intent: active ? "grid_charge_to_target" : null,
          expires_at: active ? "2026-06-27T13:00:00+00:00" : null,
          active,
          seconds_remaining: active ? 1800 : 0,
          options: OPTIONS,
        }),
      });
    });
    await page.goto("/");
    await page.getByTestId("home-more-toggle").click();
    await page.getByTestId("home-more-manual-toggle").click();
    const card = page.getByTestId("override");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("override-inactive")).toBeVisible();

    await page.getByTestId("override-intent").selectOption("grid_charge_to_target");
    await page.getByTestId("override-apply").click();
    // Charging is a risky action → a confirmation step with the consequence appears first.
    await expect(page.getByTestId("override-confirm-panel")).toBeVisible();
    await expect(page.getByTestId("override-consequence")).toContainText("grid");
    await page.getByTestId("override-confirm").click();
    await expect(page.getByTestId("override-active")).toBeVisible();
    await expect(page.getByTestId("override-active")).toContainText("Charge");

    await page.getByTestId("override-clear").click();
    await expect(page.getByTestId("override-inactive")).toBeVisible();
  });

  test("resident can set vul tot X% vóór HH:MM and cancel it", async ({ page }) => {
    let mustCharge: {
      active: boolean;
      target_soc: number | null;
      deadline_hhmm: string | null;
      unreachable_reason: string | null;
    } = {
      active: false,
      target_soc: null,
      deadline_hhmm: null,
      unreachable_reason: null,
    };
    await page.route("**/api/override", async (route) => {
      if (route.request().url().includes("/must-charge")) {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          intent: null,
          expires_at: null,
          active: false,
          seconds_remaining: 0,
          options: OPTIONS,
        }),
      });
    });
    await page.route("**/api/override/must-charge", async (route) => {
      const method = route.request().method();
      if (method === "POST") {
        const body = JSON.parse(route.request().postData() || "{}");
        mustCharge = {
          active: true,
          target_soc: body.target_soc,
          deadline_hhmm: body.deadline,
          unreachable_reason: "Dagplafond 1 kWh bereikt",
        };
      } else if (method === "DELETE") {
        mustCharge = {
          active: false,
          target_soc: null,
          deadline_hhmm: null,
          unreachable_reason: null,
        };
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...mustCharge,
          deadline: mustCharge.active ? "2026-01-16T06:00:00+00:00" : null,
          created: mustCharge.active ? "2026-01-15T22:00:00+00:00" : null,
          expires: mustCharge.active ? "2026-01-16T06:00:00+00:00" : null,
          seconds_remaining: mustCharge.active ? 8 * 3600 : 0,
          warning: mustCharge.unreachable_reason,
          watch_only: true,
          execution: "alleen kijken — niet uitgevoerd",
          deadline_format: "ISO-8601 with a timezone offset, or HH:MM",
        }),
      });
    });
    await page.goto("/");
    await page.getByTestId("home-more-toggle").click();
    await page.getByTestId("home-more-manual-toggle").click();
    await expect(page.getByTestId("must-charge")).toBeVisible();
    await expect(page.getByTestId("must-charge-watch-only")).toHaveText(
      "alleen kijken — niet uitgevoerd",
    );
    await page.getByTestId("must-charge-target").fill("80");
    await page.getByTestId("must-charge-hour").fill("07:00");
    await page.getByTestId("must-charge-submit").click();
    await expect(page.getByTestId("must-charge-active")).toContainText("Vul tot 80% vóór 07:00");
    await expect(page.getByTestId("must-charge-warning")).toHaveText("Dagplafond 1 kWh bereikt");
    await page.getByTestId("must-charge-cancel").click();
    await expect(page.getByTestId("must-charge-active")).toHaveCount(0);
  });
});
