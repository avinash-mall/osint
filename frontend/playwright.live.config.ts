import { defineConfig, devices } from '@playwright/test';

/**
 * Live-backend e2e config — drives the REAL running stack through nginx at
 * http://localhost:3000 (no mock API, unlike playwright.config.ts which uses
 * tests/visual/mockApi.ts). A `setup` project logs in once via the real
 * /api/auth/login and saves the session cookie to storageState so every spec
 * starts authenticated as admin.
 *
 * Prereqs: the Docker Compose stack is up and healthy, and ADMIN_USERNAME /
 * ADMIN_PASSWORD are resolvable (env or repo-root .env — see auth.setup.ts).
 *
 * Run:  npm run test:e2e
 */
const BASE = process.env.SENTINEL_BASE_URL || 'http://localhost:3000';
const STORAGE = 'tests/e2e/.auth/state.json';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    baseURL: BASE,
    channel: 'chrome',
    colorScheme: 'dark',
    locale: 'en-US',
    timezoneId: 'UTC',
    viewport: { width: 1280, height: 900 },
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'live',
      dependencies: ['setup'],
      use: { ...devices['Desktop Chrome'], channel: 'chrome', storageState: STORAGE },
      testIgnore: /auth\.setup\.ts/,
    },
  ],
});
