/** Admin workspace — live-backend smoke (requires admin role from auth.setup). */
import { expect, test } from '@playwright/test';
import { gotoApp, switchWorkspace, tour, clickIfVisible } from './helpers';

test('admin tabs mount and reference-platforms controls render', async ({ page }) => {
  await gotoApp(page);
  await switchWorkspace(page, 'admin');

  // The admin rail lists its views as buttons (labels may collapse to icons, so
  // assert the buttons are present rather than visibly labelled).
  for (const label of ['Ontology', 'Reference platforms', 'Processing', 'AI models']) {
    await expect(page.locator('button', { hasText: label }).first()).toBeAttached();
  }

  // Open the Reference-platforms view and assert its data-tour controls render.
  // dispatchEvent fires React's onClick even when the rail label is collapsed.
  await page.locator('button', { hasText: 'Reference platforms' }).first().dispatchEvent('click');
  await expect(tour(page, 'admin-reference-platforms')).toBeVisible({ timeout: 15_000 });
  // Seed button is present (do not click — seeding is exercised by the API suite).
  await expect(tour(page, 'admin-reference-seed-button')).toBeVisible();
});
