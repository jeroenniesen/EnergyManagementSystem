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
});
