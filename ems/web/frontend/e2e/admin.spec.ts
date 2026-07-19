import { expect, test } from "@playwright/test";

// Admin panel (auth slice 2 web, design §7 "Access & security"). Runs in the "app" project, which
// is authenticated as the e2e admin (see auth.setup.ts) against the REAL backend on the shared
// e2e DB for this project — so these tests exercise the actual /api/users + /api/invites
// endpoints, not mocks (unlike settings.spec.ts's route-mocked tests).

test.describe("admin users & invites", () => {
  test("the user list renders the signed-in e2e admin", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    // The admin panel is folded into the "Access & security" nav section's content pane (not a
    // sibling of Account any more) — open that section first.
    await page.getByTestId("group-access").click();
    const users = page.getByTestId("admin-users");
    await expect(users).toBeVisible();
    await expect(users).toContainText("e2e-admin");
    await expect(page.getByTestId("admin-invites")).toBeVisible();
  });

  test("create invite shows the accept URL once, lists it pending, then revokes it", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-access").click();

    await page.locator("#admin-invite-role").selectOption("user");
    await page.getByRole("button", { name: "Create invite" }).click();

    // The link is shown exactly once, right after minting.
    const minted = page.getByTestId("admin-invite-minted");
    await expect(minted).toBeVisible();
    const urlInput = page.getByLabel("Invite link");
    const url = await urlInput.inputValue();
    expect(url).toContain("/#/accept-invite?code=");

    // Copy button works (falls back gracefully if clipboard permissions are unavailable in CI).
    await page.getByRole("button", { name: "Copy" }).click();

    // It shows up in the pending-invites list.
    const list = page.getByTestId("admin-invites-list");
    await expect(list).toBeVisible();
    await expect(list).toContainText("user");

    // Revoke it — the row disappears (best-effort refetch after the DELETE).
    const row = list.locator('[data-testid^="admin-invite-"]').first();
    await row.getByRole("button", { name: /Revoke/ }).click();
    await expect(row).toHaveCount(0);
  });
});
