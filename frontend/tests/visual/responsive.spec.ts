import { expect, test, type Page } from '@playwright/test';
import { installMockApi } from './mockApi';

async function stabilize(page: Page) {
  await page.addStyleTag({ content: `
    *, *::before, *::after { animation-duration: 0s !important; animation-delay: 0s !important; transition-duration: 0s !important; caret-color: transparent !important; }
    .leaflet-control-container { display: none !important; }
  ` });
}

async function openAuthed(page: Page) {
  await installMockApi(page, { authenticated: true });
  await page.goto('/');
  await expect(page.getByTitle('Geoint')).toBeVisible({ timeout: 10_000 });
  await stabilize(page);
}

test.describe('responsive visual regression', () => {
  test('login shell stays composed on a narrow viewport', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installMockApi(page, { authenticated: false });
    await page.goto('/');
    await expect(page.getByText('Resume operations')).toBeVisible();
    await stabilize(page);
    await expect(page).toHaveScreenshot('login-mobile.png');
  });

  test('map workspace adapts at compact and wide widths', async ({ page }) => {
    await page.setViewportSize({ width: 430, height: 900 });
    await openAuthed(page);
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('sentinel:jump-to-detection', { detail: { id: 1 } })));
    await expect(page.getByText('Tank').first()).toBeVisible();
    await expect(page).toHaveScreenshot('map-compact.png');
    await page.setViewportSize({ width: 1365, height: 900 });
    await expect(page).toHaveScreenshot('map-wide.png');
  });

  test('graph and admin workspaces keep their structure under pressure', async ({ page }) => {
    await page.setViewportSize({ width: 760, height: 900 });
    await openAuthed(page);
    await page.getByTitle('Link Graph').click();
    await expect(page.getByText('Link Graph · 2-hop neighborhood')).toBeVisible();
    await expect(page).toHaveScreenshot('graph-medium.png');
    await page.getByTitle('Admin').click();
    await expect(page.getByText('Ontology Admin')).toBeVisible();
    await expect(page).toHaveScreenshot('admin-medium.png');
  });

  test('fmv workspace keeps transport and side panel usable', async ({ page }) => {
    await page.setViewportSize({ width: 760, height: 900 });
    await openAuthed(page);
    await page.getByTitle('Drone Video').click();
    await expect(page.getByText('Tracks')).toBeVisible();
    await page.getByRole('button', { name: /^Clips/ }).click();
    await page.getByText('visual-sortie-07.mp4').click();
    await expect(page.getByText('visual-sortie-07.mp4', { exact: true })).toBeVisible();
    await expect(page).toHaveScreenshot('fmv-medium.png');
  });
});
