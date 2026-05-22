/**
 * Basemap composition — functional (non-screenshot) checks for the
 * SAT / BASE / TERRAIN layer stack in MapStage.
 *
 * Intended model: the COG imagery is the analyst's ground truth and renders
 * at the bottom (zIndex 200); BASE/TERRAIN add the cartographic basemap as a
 * reference overlay on top (zIndex 300); the opacity slider fades the overlay.
 * SAT mode = imagery alone. See docs/decisions/why-basemap-overlay-composition.md.
 */
import { expect, test, type Page } from '@playwright/test';
import { installMockApi } from './mockApi';

async function openMap(page: Page) {
  await page.setViewportSize({ width: 1365, height: 900 });
  await installMockApi(page, { authenticated: true });
  await page.goto('/');
  await expect(page.locator('.map-left-panel')).toBeVisible({ timeout: 10_000 });
  await page.addStyleTag({ content: `
    *, *::before, *::after { animation-duration: 0s !important; animation-delay: 0s !important; transition-duration: 0s !important; }
  ` });
}

/** Click the single mocked imagery scene — this sets selectedImagery and
 *  flips the basemap mode to SAT. */
async function loadImagery(page: Page) {
  await page.locator('.sentinel-row', { hasText: 'visual-pass-05' }).click();
}

const tile = (page: Page, frag: string) =>
  page.locator(`img.leaflet-tile[src*="${frag}"]`);

/** Read a computed style property off the `.leaflet-layer` container that
 *  holds the tile matching `frag` (Leaflet sets opacity/zIndex there, not on
 *  individual tile <img> elements). */
async function layerStyle(page: Page, frag: string, prop: string) {
  return page.evaluate(({ frag, prop }) => {
    const el = document.querySelector(`img.leaflet-tile[src*="${frag}"]`);
    const layer = el?.closest('.leaflet-layer');
    return layer ? getComputedStyle(layer).getPropertyValue(prop) : null;
  }, { frag, prop });
}

test.describe('Basemap composition', () => {
  test('SAT mode renders imagery only, no cartographic basemap', async ({ page }) => {
    await openMap(page);
    await loadImagery(page);
    await page.locator('.layer-panel-basemap-tile', { hasText: 'SAT' }).click();

    await expect(tile(page, '/cog/tiles/').first()).toBeVisible();
    await expect(tile(page, '/basemap/')).toHaveCount(0);
    await expect(tile(page, '/terrain/')).toHaveCount(0);
  });

  test('BASE mode renders imagery + carto overlay on top', async ({ page }) => {
    await openMap(page);
    await loadImagery(page);
    await page.locator('.layer-panel-basemap-tile', { hasText: 'BASE' }).click();

    await expect(tile(page, '/cog/tiles/').first()).toBeVisible();
    await expect(tile(page, '/basemap/').first()).toBeVisible();

    // Carto overlay (zIndex 300) sits above the SAT imagery (zIndex 200).
    const cartoZ = Number(await layerStyle(page, '/basemap/', 'z-index'));
    const satZ = Number(await layerStyle(page, '/cog/tiles/', 'z-index'));
    expect(cartoZ).toBeGreaterThan(satZ);
  });

  test('BASE opacity slider fades the carto overlay, not the imagery', async ({ page }) => {
    await openMap(page);
    await loadImagery(page);
    await page.locator('.layer-panel-basemap-tile', { hasText: 'BASE' }).click();
    await expect(tile(page, '/basemap/').first()).toBeVisible();

    // step="0.05" uniquely identifies the basemap-opacity slider (the
    // confidence and time-machine sliders use finer steps).
    await page.locator('input[type="range"][step="0.05"]').fill('0.2');

    await expect.poll(async () =>
      Number(await layerStyle(page, '/basemap/', 'opacity')),
    ).toBeCloseTo(0.2, 1);
    expect(Number(await layerStyle(page, '/cog/tiles/', 'opacity'))).toBeCloseTo(1, 1);
  });

  test('TERRAIN mode renders imagery + terrain overlay on top', async ({ page }) => {
    await openMap(page);
    await loadImagery(page);
    await page.locator('.layer-panel-basemap-tile', { hasText: 'TERRAIN' }).click();

    await expect(tile(page, '/cog/tiles/').first()).toBeVisible();
    await expect(tile(page, '/terrain/').first()).toBeVisible();

    const terrainZ = Number(await layerStyle(page, '/terrain/', 'z-index'));
    const satZ = Number(await layerStyle(page, '/cog/tiles/', 'z-index'));
    expect(terrainZ).toBeGreaterThan(satZ);
  });
});
