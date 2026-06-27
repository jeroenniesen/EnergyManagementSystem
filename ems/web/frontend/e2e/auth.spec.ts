import { expect, test } from "@playwright/test";

const SCHEMA = [
  {
    key: "ui.theme", label: "Theme", type: "enum", default: "auto",
    group: "ui", help: "", min: null, max: null,
    options: ["auto", "dark", "light"], step: null, unit: "",
  },
];
const BASE: Record<string, string> = { "ui.theme": "auto" };

test.describe("EMS access token", () => {
  test("real backend reports auth not required in dev", async ({ request }) => {
    const b = await (await request.get("/api/auth")).json();
    expect(b).toMatchObject({ required: false, authenticated: true });
  });

  test("Access section appears when protected and the token is sent on writes", async ({
    page,
  }) => {
    let token: string | null = null;
    let authedHeader: string | undefined;
    await page.route("**/api/auth", async (route) => {
      authedHeader = route.request().headers()["authorization"];
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ required: true, authenticated: Boolean(authedHeader) }),
      });
    });
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        token = route.request().headers()["authorization"] ?? null;
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ values: BASE }),
        });
      } else {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: SCHEMA, values: BASE }),
        });
      }
    });

    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    // The Access section only shows because /api/auth said required.
    await expect(page.getByTestId("settings-access")).toBeVisible();
    await page.getByTestId("access-token").fill("s3cret");
    await page.getByTestId("access-token-save").click();

    // Now change a setting and save — the write must carry the Bearer token.
    await page.locator("#set-ui\\.theme").selectOption("dark");
    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    expect(token).toBe("Bearer s3cret");
  });
});
