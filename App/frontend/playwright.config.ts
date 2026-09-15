import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 30000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8766",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "../.venv/bin/python ../scripts/serve_ui_tests.py",
    url: "http://127.0.0.1:8766/api/health",
    env: {
      SUMRADIO_PORT: "8766",
      SUMRADIO_DATA_DIR: `../.sumradio-data/e2e-${process.pid}`,
    },
    reuseExistingServer: false,
    timeout: 30000,
  },
});
