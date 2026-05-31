/** Drone Video (FMV) workspace — live-backend smoke. */
import { expect, test } from '@playwright/test';
import { gotoApp, switchWorkspace } from './helpers';

test('fmv workspace renders and lists clips', async ({ page }) => {
  await gotoApp(page);
  await switchWorkspace(page, 'fmv');
  // The side panel defaults to the Clips tab for a fresh visitor; the word
  // "clip" appears in the panel header / empty state regardless of data.
  await expect(page.getByText(/clip/i).first()).toBeVisible({ timeout: 15_000 });

  // If at least one real clip row is present, selecting it should mount the
  // player controls; otherwise the empty state is acceptable.
  const clipRow = page.locator('.clip-row').first();
  if (await clipRow.count()) {
    // An admin delete control sits next to each clip row (do not click it —
    // that would delete a real clip; just assert it is present).
    await expect(page.locator('[data-tour="clip-delete"]').first()).toBeVisible();
    await clipRow.click();
    await expect(page.locator('button[title*="Play"]').first()).toBeVisible({ timeout: 15_000 });
  } else {
    test.info().annotations.push({ type: 'note', description: 'no clips uploaded' });
  }
});
