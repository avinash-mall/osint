/**
 * Auth setup project — performs a REAL login against the running backend and
 * saves the session cookie to storageState, so the `live` project's specs all
 * start authenticated as admin. Credentials come from ADMIN_USERNAME /
 * ADMIN_PASSWORD in the environment, falling back to the repo-root .env.
 */
import { test as setup, expect } from '@playwright/test';
import { existsSync, readFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const STORAGE = 'tests/e2e/.auth/state.json';

function creds(): { username: string; password: string } {
  let username = process.env.ADMIN_USERNAME;
  let password = process.env.ADMIN_PASSWORD;
  if (!username || !password) {
    // Playwright runs with cwd = frontend/; the repo-root .env is one level up.
    const envPath = resolve(process.cwd(), '../.env');
    if (existsSync(envPath)) {
      for (const line of readFileSync(envPath, 'utf8').split('\n')) {
        const m = line.match(/^([A-Z_]+)=(.*)$/);
        if (!m) continue;
        const val = m[2].trim().replace(/^['"]|['"]$/g, '');
        if (m[1] === 'ADMIN_USERNAME') username = username || val;
        if (m[1] === 'ADMIN_PASSWORD') password = password || val;
      }
    }
  }
  if (!username || !password) {
    throw new Error('ADMIN_USERNAME/ADMIN_PASSWORD not found in env or repo-root .env');
  }
  return { username, password };
}

setup('authenticate as admin', async ({ request }) => {
  const { username, password } = creds();
  const resp = await request.post('/api/auth/login', { data: { username, password } });
  expect(resp.ok(), `login failed: ${resp.status()}`).toBeTruthy();
  const body = await resp.json();
  expect(body.role, 'admin role expected to unlock Admin workspace').toBe('admin');

  // Confirm the session cookie is valid before persisting it.
  const me = await request.get('/api/auth/me');
  expect(me.ok(), 'session cookie not accepted by /api/auth/me').toBeTruthy();

  mkdirSync(dirname(STORAGE), { recursive: true });
  await request.storageState({ path: STORAGE });
});
