/**
 * Task 1.3 — detector-provenance chip.
 *
 * The SelectionPanel header must render a [DETECTOR] chip showing which
 * model produced the detection (SAM 3, DOTA-OBB, …) and visually
 * distinguish single-detector calls (neutral) from multi-detector WBF
 * agreement (info / blue).
 *
 * ProvenancePanel is currently orphan-mounted (no caller in frontend/src/);
 * the third test below is skipped until the SelectionPanel `Provenance` tab
 * wires it in. The underlying detectionProvenance() helper should be covered
 * by a Vitest unit test once frontend/ grows a Vitest config.
 */
import { expect, test, type Page } from '@playwright/test';
import { installMockApi } from './mockApi';

async function openMapAndSelectDetection(page: Page) {
  await page.addInitScript(() => {
    try { localStorage.setItem('sentinel:tour-completed', '1'); } catch { /* ignore */ }
  });
  await page.goto('/');
  await expect(page.getByTitle('Map', { exact: true })).toBeVisible({ timeout: 10_000 });
  await page.waitForTimeout(500);
}

test.describe('Detector provenance chip', () => {
  test('single-detector SAM 3 detection renders neutral chip + "alone" tooltip', async ({ page }) => {
    await installMockApi(page, {
      authenticated: true,
      detectionOverrides: {
        original_class: 'plane',
        parent_class: 'aircraft',
        label: 'Aircraft',
        display_label: 'Aircraft',
        source_layer: 'sam3',
        // No wbf_member_sources -> single-detector
      },
    });
    await openMapAndSelectDetection(page);

    const detectionPath = page.locator('path.leaflet-interactive').first();
    await detectionPath.waitFor({ state: 'attached', timeout: 10_000 });
    await detectionPath.click({ force: true });

    const chip = page.getByTestId('detector-provenance-chip');
    await expect(chip).toBeVisible({ timeout: 10_000 });
    await expect(chip).toHaveText(/SAM 3/i);
    // No "+N" suffix when alone.
    await expect(chip).not.toHaveText(/\+\d/);
    // Neutral chip — no `info` modifier class.
    const klass = await chip.getAttribute('class');
    expect(klass || '').not.toMatch(/\binfo\b/);
    const tooltip = await chip.getAttribute('title');
    expect(tooltip || '').toMatch(/Single-detector call/);
  });

  test('multi-detector SAM3+DOTA detection renders info-styled chip + "+1" badge', async ({ page }) => {
    await installMockApi(page, {
      authenticated: true,
      detectionOverrides: {
        original_class: 'plane',
        parent_class: 'aircraft',
        label: 'Aircraft',
        display_label: 'Aircraft',
        source_layer: 'sam3',
        wbf_member_sources: ['sam3', 'dota_obb'],
        wbf_member_count: 2,
      },
    });
    await openMapAndSelectDetection(page);

    const detectionPath = page.locator('path.leaflet-interactive').first();
    await detectionPath.waitFor({ state: 'attached', timeout: 10_000 });
    await detectionPath.click({ force: true });

    const chip = page.getByTestId('detector-provenance-chip');
    await expect(chip).toBeVisible({ timeout: 10_000 });
    await expect(chip).toHaveText(/SAM 3/i);
    await expect(chip).toHaveText(/\+1/);
    const klass = await chip.getAttribute('class');
    expect(klass || '').toMatch(/\binfo\b/);
    const tooltip = await chip.getAttribute('title');
    expect(tooltip || '').toMatch(/Multi-detector agreement/);
    expect(tooltip || '').toMatch(/2 detectors agreed/);
  });

  // TODO: when the SelectionPanel `Provenance` tab is wired to render
  // ProvenancePanel (follow-up to Task 1.3), replace this skip with a real
  // DOM assertion that clicks into the Provenance tab and reads the
  // "Detector ensemble" Kv rows. The earlier page.evaluate version was
  // removed because it re-implemented detectionProvenance() inside the
  // test, so a bug in _helpers.ts could not break it. Frontend has no
  // Vitest setup yet — once one lands, unit-test the helper there instead.
  test.skip('Detector ensemble panel renders via mounted ProvenancePanel', async () => {
    // intentionally empty — see TODO above.
  });
});
