import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  // The single workflow deliberately includes two Electron launches, a backend
  // process restart, Git remotes, Agent streaming and six export formats. On a
  // Windows software-rendered CI session it can exceed two minutes even when
  // every individual assertion is healthy.
  timeout: 300_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  outputDir: "../../../test-results/electron",
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
