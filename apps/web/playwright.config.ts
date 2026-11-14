import { defineConfig, devices } from "@playwright/test";

/** E2E journeys against a running Compose stack (US-130). Start the stack first; see deploy/compose/compose.yaml. */
export default defineConfig({
  testDir: "./e2e",
  // Screenshot baselines (US-148) sit beside the specs, one folder per spec, named by the platform they were taken on.
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFileName}/{arg}-{projectName}-{platform}{ext}",
  timeout: 60_000,
  retries: 0,
  // One worker: the journeys change shared data on the dev stack, and the screenshot and LCP gates must not run beside
  // them (US-148).
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.TELA_E2E_BASE_URL ?? "http://tela.localhost:8088",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
