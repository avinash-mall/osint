import { expect, test, type Page } from '@playwright/test';
import { installMockApi } from './mockApi';

async function openMap(page: Page) {
  await page.setViewportSize({ width: 1365, height: 900 });
  await installMockApi(page, { authenticated: true });
  await page.goto('/');
  // 'map' is the default workspace — no nav click needed (clicking the rail
  // would focus it and keep it expanded over the panel).
  await expect(page.locator('.map-left-panel')).toBeVisible({ timeout: 10_000 });
  await page.addStyleTag({ content: `
    *, *::before, *::after { animation-duration: 0s !important; animation-delay: 0s !important; transition-duration: 0s !important; caret-color: transparent !important; }
    .leaflet-control-container { display: none !important; }
  ` });
}

test.describe('LayerPanel revamp', () => {
  test('basemap gallery renders three thumbnails', async ({ page }) => {
    await openMap(page);
    await expect(page.locator('.layer-panel-basemap-tile')).toHaveCount(3);
  });

  test('selecting a basemap shows the check chip', async ({ page }) => {
    await openMap(page);
    await page.locator('.layer-panel-basemap-tile', { hasText: 'SAT' }).click();
    await expect(
      page.locator('.layer-panel-basemap-tile.is-active .layer-panel-basemap-check'),
    ).toBeVisible();
  });

  test('clicking an overlay row toggles it off', async ({ page }) => {
    await openMap(page);
    const row = page.locator('.layer-panel-overlay-row', { hasText: 'AI Detections' });
    await expect(row).toHaveAttribute('aria-pressed', 'true');
    await row.click();
    await expect(row).toHaveAttribute('aria-pressed', 'false');
  });

  test('analytics tools render as three locked rows', async ({ page }) => {
    await openMap(page);
    await expect(page.locator('.layer-panel-overlay-row.is-disabled .lucide-lock')).toHaveCount(3);
  });

  test('panel keeps its default layout', async ({ page }) => {
    await openMap(page);
    await expect(page.locator('.map-left-panel')).toHaveScreenshot('layer-panel-default.png');
  });
});
