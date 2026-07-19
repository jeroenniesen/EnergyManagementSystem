// Fixed access token for the authenticated e2e "app" project. The app webServer is started with
// EMS_WEB_TOKEN set to this value; auth.setup.ts onboards the first admin passing it as the
// shared_token, which migrates it into an admin ACCESS token (design §8). That same value is then a
// valid Bearer for every request in the app project. Test-only; never a real secret.
export const E2E_ACCESS_TOKEN = "e2e-fixed-admin-token-not-a-secret";
