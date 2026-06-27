import { defineConfig } from "@playwright/test";

// Validates the live EMS: API contract + the rendered dashboard (GOAL §6 visual gate).
// Assumes the FastAPI app (serving the built SPA from ems/web/static/dist) is already
// running at :8080. The caller starts it; reuseExistingServer keeps it simple.
export default defineConfig({
  testDir: "./e2e",
  timeout: 15000,
  use: {
    // Port 8099 for tests (8080 may be taken by another local app); the app's default stays 8080.
    baseURL: "http://127.0.0.1:8099",
    trace: "on-first-retry",
  },
  reporter: [["list"]],
  // Start the FastAPI app (serving the built SPA from ems/web/static/dist) for the test run.
  webServer: {
    command: "uv run uvicorn ems.main:app --host 127.0.0.1 --port 8099",
    cwd: "/Users/jeroenniesen/Development/EnergyManagementSystem",
    url: "http://127.0.0.1:8099/health/live",
    reuseExistingServer: false,
    timeout: 60000,
    env: { PATH: `${process.env.HOME}/.local/bin:${process.env.PATH ?? ""}` },
  },
});

