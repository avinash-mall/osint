/** Ingest workspace — live-backend smoke. */
import { expect, test } from '@playwright/test';
import { gotoApp, switchWorkspace } from './helpers';

test('ingest workspace renders upload controls', async ({ page }) => {
  await gotoApp(page);
  await switchWorkspace(page, 'ingest');
  // A file <input> is the core of the upload workspace.
  await expect(page.locator('input[type="file"]').first()).toBeAttached();
  // Media-type / sensor selectors are present.
  await expect(page.getByText(/media type/i).first()).toBeVisible();
  await expect(page.locator('select').first()).toBeVisible();
});
