import { expect, type Locator, type Page } from '@playwright/test';

/** Workspace nav buttons render as <button title="..."> in the Shell rail. */
export const WORKSPACE_TITLE = {
  ingest: 'Ingest',
  map: 'Map',
  fmv: 'Drone Video',
  graph: 'Link Graph',
  admin: 'Admin',
} as const;

export async function gotoApp(page: Page) {
  // Suppress the first-visit Product Tour welcome modal — its .confirm-overlay
  // intercepts every click. (The product-tour test opens the tour explicitly.)
  await page.addInitScript(() => {
    try { localStorage.setItem('sentinel:tour-completed', '1'); } catch { /* ignore */ }
  });
  await page.goto('/');
  // The authenticated shell renders the workspace nav; the login screen would
  // not. storageState (auth.setup.ts) keeps us logged in.
  await expect(page.locator('button[title="Map"]')).toBeVisible({ timeout: 20_000 });
  // Kill animations so visibility/clicks are deterministic.
  await page.addStyleTag({ content: `*,*::before,*::after{animation-duration:0s!important;transition-duration:0s!important;}` });
}

export async function switchWorkspace(page: Page, key: keyof typeof WORKSPACE_TITLE) {
  const btn = page.locator(`button[title="${WORKSPACE_TITLE[key]}"]`);
  await btn.click();
  await expect(btn).toHaveAttribute('aria-current', 'page');
}

/** Several controls render twice (desktop rail + collapsed mobile "lip"); target
 *  the on-screen instance so clicks/assertions hit what the operator sees. */
export const tour = (page: Page, id: string): Locator => page.locator(`[data-tour="${id}"]:visible`).first();

/** Click a control only if it is currently rendered+visible; returns whether it acted.
 *  Used for state-dependent controls (selection panel, prithvi overlays, etc.). */
export async function clickIfVisible(loc: Locator): Promise<boolean> {
  if (await loc.count() === 0) return false;
  if (!(await loc.first().isVisible())) return false;
  await loc.first().click();
  return true;
}
