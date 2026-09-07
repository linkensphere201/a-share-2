import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  outputDir: '../.tmp/test/playwright-output',
  fullyParallel: false,
  workers: 1,
  reporter: 'line',
  webServer: [
    {
      command: '.\\.venv\\Scripts\\python.exe -m uvicorn --app-dir src stock_harness.api:app --host 127.0.0.1 --port 8001',
      cwd: '..',
      port: 8001,
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: 'npm.cmd run dev',
      port: 5173,
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    headless: true,
    viewport: { width: 1440, height: 900 },
  },
})
