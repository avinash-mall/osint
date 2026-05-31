/** Link Graph workspace — live-backend smoke. */
import { expect, test } from '@playwright/test';
import { gotoApp, switchWorkspace } from './helpers';

test('graph workspace renders search, mode tabs and stats', async ({ page }) => {
  await gotoApp(page);
  await switchWorkspace(page, 'graph');

  await expect(page.getByPlaceholder(/search entity/i)).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[role="tablist"][aria-label="Graph mode"]')).toBeVisible();
  await expect(page.getByText(/NODES/).first()).toBeVisible();

  // Switching graph mode is a real interaction.
  const tabs = page.locator('[role="tablist"][aria-label="Graph mode"] [role="tab"]');
  if (await tabs.count() > 1) {
    await tabs.nth(1).click();
    await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
  }
});
