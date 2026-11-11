import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 55_000,
  use: {
    baseURL: process.env.ZADDESH_E2E_URL ?? 'http://127.0.0.1:18765',
    launchOptions: process.env.ZADDESH_BROWSER ? { executablePath: process.env.ZADDESH_BROWSER } : {},
  },
});
