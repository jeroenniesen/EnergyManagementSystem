import { expect, test } from "@playwright/test";

test("invoice comparison reports missing coverage without inventing a total", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("nav-insights").click();
  const tools = page.getByTestId("tariff-tools");
  await expect(tools).toBeVisible();
  await tools.getByLabel("Invoice start date").fill("2020-01-01");
  await tools.getByLabel("Invoice end date (exclusive)").fill("2020-02-01");
  await tools.getByLabel("Invoice amount (€)").fill("120");
  await tools.getByLabel("Fixed costs for this period (€)").fill("20");
  await tools.getByRole("button", { name: "Compare invoice" }).click();
  await expect(tools.getByTestId("invoice-result")).toContainText("Full invoice comparison unavailable");
  await expect(tools.getByTestId("invoice-result")).toContainText("0% observed");
});

test("date-effective tariff form keeps all contract components explicit", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("nav-insights").click();
  const tools = page.getByTestId("tariff-tools");
  await tools.getByText("Add a tariff period", { exact: true }).click();
  await tools.getByLabel("Tariff start date").fill("2090-01-01");
  await tools.getByLabel("Tariff end date (exclusive)").fill("2091-01-01");
  await tools.getByRole("combobox", { name: "Provider price includes import tax and surcharge", exact: true }).selectOption("yes");
  await tools.getByLabel("Import tax (€/kWh)", { exact: true }).fill("0.12");
  await tools.getByLabel("Import surcharge (€/kWh)", { exact: true }).fill("0.02");
  await tools.getByLabel("Export tax (€/kWh)", { exact: true }).fill("0");
  await tools.getByLabel("Export adjustment (€/kWh)", { exact: true }).fill("0");
  await tools.getByLabel("Export fee (€/kWh)", { exact: true }).fill("0.01");
  await tools.getByRole("button", { name: "Save tariff period" }).click();
  await expect(tools.getByTestId("tariff-period-list")).toContainText("2090-01-01");
  await expect(tools.getByTestId("tariff-period-list")).toContainText("2091-01-01");
  await page.reload();
  await page.getByTestId("nav-insights").click();
  await expect(page.getByTestId("tariff-period-list")).toContainText("2090-01-01");
});
