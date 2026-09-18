import { expect, test } from "@playwright/test";

test("bill advice shows honest missing evidence and schedules an appliance without writes", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("nav-insights").click();
  await expect(page.getByTestId("bill-advice")).toBeVisible();
  await expect(page.getByTestId("bill-advice")).toContainText("Advice only");
  await expect(page.getByTestId("battery-calibration")).toContainText("Need three");
  await page.getByLabel("Run duration (minutes)").fill("60");
  await page.getByLabel("Energy per run (kWh)").fill("1");
  await page.getByRole("button", { name: "Find a cheaper window" }).click();
  await expect(page.getByTestId("appliance-result")).toContainText("Estimated");
});

test("bill advice handles unavailable service without a false zero", async ({ page }) => {
  await page.route("**/api/bill-advice", route => route.fulfill({status:503, body:"unavailable"}));
  await page.goto("/");
  await page.getByTestId("nav-insights").click();
  await expect(page.getByTestId("bill-advice-error")).toContainText("couldn’t load");
  await expect(page.getByTestId("bill-advice-error")).not.toContainText("€0");
});

test("bill tools fit a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByTestId("nav-insights").click();
  const advice = page.getByTestId("bill-advice");
  await expect(advice).toBeVisible();
  await expect(page.getByTestId("battery-calibration")).toContainText("Need three");
  await advice.screenshot({ path: test.info().outputPath("bill-advice-mobile.png") });
  const tariffs = page.getByTestId("tariff-tools");
  await tariffs.getByText("Add a tariff period", { exact: true }).click();
  await tariffs.screenshot({ path: test.info().outputPath("tariffs-mobile.png") });
  for (const panel of [advice, tariffs]) {
    const bounds = await panel.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  }
});
