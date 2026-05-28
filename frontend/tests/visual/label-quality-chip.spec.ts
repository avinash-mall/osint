/**
 * Task 1.2 — generic vs specific label quality chip.
 *
 * The SelectionPanel must render a "(generic)" label and a [GENERIC] chip
 * for DOTA-OBB generic detections that the ontology would otherwise
 * tie-break to a fabricated specific class name. Verifier-confirmed
 * detections render a [VERIFIED] chip. The "inferred" default renders
 * neither.
 *
 * See docs/decisions/why-generic-labels-when-unverified.md.
 */
import { expect, test, type Page } from '@playwright/test';
import { installMockApi } from './mockApi';

async function openMapAndSelectDetection(page: Page) {
  // Suppress the first-run welcome/tour modal so the map UI stays clickable.
  await page.addInitScript(() => {
    try { localStorage.setItem('sentinel:tour-completed', '1'); } catch { /* ignore */ }
  });
  await page.goto('/');
  await expect(page.getByTitle('Map', { exact: true })).toBeVisible({ timeout: 10_000 });
  // The mocked GeoJSON ships exactly one detection. Click the dataset row in
  // the LayerPanel to seed selection, then open the SelectionPanel header.
  // The simplest path: directly invoke the selection by clicking the first
  // detection marker on the map after the layer renders.
  await page.waitForTimeout(500);
  // Use a deterministic hook: the SelectionPanel reads from a global event
  // store. For our purposes, we open the SelectionPanel via the right-rail
  // detection row.
}

test.describe('Label-quality chip', () => {
  test('generic DOTA-OBB detection renders "(generic)" label and [GENERIC] chip', async ({ page }) => {
    await installMockApi(page, {
      authenticated: true,
      detectionOverrides: {
        original_class: 'plane',
        parent_class: 'aircraft',
        label: 'Aircraft (generic)',
        display_label: 'Aircraft (generic)',
        label_quality: 'generic',
      },
    });
    await openMapAndSelectDetection(page);

    // Click the detection feature on the map. The layer renders one polygon
    // tagged with class 'plane'. We use the leaflet pane to find a path.
    const detectionPath = page.locator('path.leaflet-interactive').first();
    await detectionPath.waitFor({ state: 'attached', timeout: 10_000 });
    await detectionPath.click({ force: true });

    const chip = page.getByTestId('label-quality-chip');
    await expect(chip).toBeVisible({ timeout: 10_000 });
    await expect(chip).toHaveText(/generic/i);
    // Title is the policy-resolved "(generic)" string.
    await expect(page.locator('text=/Aircraft \\(generic\\)/i').first()).toBeVisible();
  });

  test('verified detection renders [VERIFIED] chip', async ({ page }) => {
    await installMockApi(page, {
      authenticated: true,
      detectionOverrides: {
        original_class: 'plane',
        parent_class: 'aircraft',
        label: 'Fighter Aircraft',
        display_label: 'Fighter Aircraft',
        label_quality: 'verified',
      },
    });
    await openMapAndSelectDetection(page);

    const detectionPath = page.locator('path.leaflet-interactive').first();
    await detectionPath.waitFor({ state: 'attached', timeout: 10_000 });
    await detectionPath.click({ force: true });

    const chip = page.getByTestId('label-quality-chip');
    await expect(chip).toBeVisible({ timeout: 10_000 });
    await expect(chip).toHaveText(/verified/i);
  });

  test('inferred detection renders no chip', async ({ page }) => {
    await installMockApi(page, {
      authenticated: true,
      detectionOverrides: {
        original_class: 'tank',
        parent_class: 'vehicle',
        label: 'Tank',
        display_label: 'Tank',
        label_quality: 'inferred',
      },
    });
    await openMapAndSelectDetection(page);

    const detectionPath = page.locator('path.leaflet-interactive').first();
    await detectionPath.waitFor({ state: 'attached', timeout: 10_000 });
    await detectionPath.click({ force: true });

    // The SelectionPanel header still renders; just no chip.
    await expect(page.locator('text=/Tank/i').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('label-quality-chip')).toHaveCount(0);
  });
});
