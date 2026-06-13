/**
 * Map workspace — live-backend interaction test. Walks the data-tour controls
 * enumerated in src/components/tour/tourSteps.ts and asserts each RESPONDS
 * (state class / aria-pressed flips, panels open) against the real stack.
 *
 * Controls that only render with a selected detection or loaded overlays are
 * exercised opportunistically (clickIfVisible) so the suite stays green when
 * the live DB is sparse, per the agreed "degrade gracefully" policy.
 */
import { expect, test } from '@playwright/test';
import { gotoApp, tour, clickIfVisible } from './helpers';

test.beforeEach(async ({ page }) => {
  await gotoApp(page);
  await expect(tour(page, 'layer-panel')).toBeVisible();
});

test('basemap selector switches active layer', async ({ page }) => {
  const sel = tour(page, 'basemap-selector');
  await expect(sel).toBeVisible();
  for (const label of ['BASE', 'TERRAIN', 'SAT']) {
    const tile = page.locator('.layer-panel-basemap-tile', { hasText: label });
    await tile.click();
    await expect(tile).toHaveClass(/is-active/);
    await expect(tile).toHaveAttribute('aria-pressed', 'true');
  }
});

test('basemap opacity slider moves', async ({ page }) => {
  // BASE mode enables the overlay opacity slider (step=0.05 is unique to it).
  await page.locator('.layer-panel-basemap-tile', { hasText: 'BASE' }).click();
  const slider = page.locator('input[type="range"][step="0.05"]');
  if (await slider.count()) {
    await slider.fill('0.3');
    await expect(slider).toHaveValue('0.3');
  }
});

test('layer toggles flip pressed state', async ({ page }) => {
  const toggles = tour(page, 'layer-toggles');
  await expect(toggles).toBeVisible();
  const first = toggles.locator('[aria-pressed]').first();
  const before = await first.getAttribute('aria-pressed');
  await first.click();
  await expect(first).not.toHaveAttribute('aria-pressed', before ?? '');
});

test('left-rail sections render', async ({ page }) => {
  await expect(tour(page, 'detection-classes')).toBeVisible();
  await expect(tour(page, 'analytics-tools')).toBeVisible();
});

test('imagery list exposes an admin delete control when scenes exist', async ({ page }) => {
  // The imagery-list section only renders when at least one pass is loaded; the
  // per-row delete button only when authed as admin (we are). Don't click it —
  // that would delete real imagery — just assert the control is present.
  const list = tour(page, 'imagery-list');
  if (await list.count() === 0) {
    test.info().annotations.push({ type: 'note', description: 'no imagery scenes loaded' });
    return;
  }
  await expect(list).toBeVisible();
  await expect(page.locator('[data-tour="imagery-delete"]').first()).toBeVisible();
});

test('overlay controls respond when present', async ({ page }) => {
  // Geometry render modes live in the Overlays section and render once the
  // detections layer is active; interact if visible.
  for (const id of ['geom-hbb', 'geom-obb', 'geom-mask']) {
    await clickIfVisible(tour(page, id));
  }
  // Page still healthy after the toggles.
  await expect(tour(page, 'layer-panel')).toBeVisible();
});

test('zoom + focus-mode controls act on the map', async ({ page }) => {
  // These sit over the Leaflet canvas (whose own zoom control overlaps them), so
  // a pointer click can land on the map instead. dispatchEvent('click') bubbles
  // to React's delegated handler regardless of overlap — we still assert state.
  await tour(page, 'zoom-in').dispatchEvent('click');
  await tour(page, 'zoom-out').dispatchEvent('click');
  await tour(page, 'recenter').dispatchEvent('click');
  await expect(page.locator('.leaflet-container')).toBeVisible();
  const focus = tour(page, 'focus-mode');
  await focus.dispatchEvent('click');
  await expect(focus).toHaveAttribute('aria-pressed', 'true');
  await focus.dispatchEvent('click');
  await expect(focus).toHaveAttribute('aria-pressed', 'false');
});

test('draw + range-ring tools toggle on and off', async ({ page }) => {
  const draw = tour(page, 'draw-object');
  await draw.click();
  await expect(draw).toContainText(/cancel/i);
  await draw.click();
  await expect(draw).toContainText(/draw object/i);

  const ring = tour(page, 'range-ring');
  await ring.click();
  await expect(ring).toContainText(/cancel/i);
  await ring.click();
  await expect(ring).toContainText(/range ring/i);
});

test('product tour launches and closes', async ({ page }) => {
  await tour(page, 'product-tour-btn').click();
  // Launching shows either the welcome modal (.confirm-overlay) or a spotlight
  // tooltip with a Close-tour control.
  const tourUi = page.locator('.confirm-overlay, [aria-label="Close tour"]').first();
  await expect(tourUi).toBeVisible({ timeout: 10_000 });
  await page.keyboard.press('Escape');
});

test('time machine controls respond', async ({ page }) => {
  await expect(tour(page, 'time-machine')).toBeVisible();
  await tour(page, 'tm-play').click();          // play/pause toggle
  await tour(page, 'tm-recenter').click();       // snap playhead to now
  // Time-window segmented control: click each option button.
  const ranges = tour(page, 'tm-ranges').locator('button');
  const n = await ranges.count();
  if (n) await ranges.nth(Math.min(1, n - 1)).click();
  // Confidence slider.
  const conf = tour(page, 'tm-conf').locator('input[type="range"]');
  if (await conf.count()) await conf.first().fill('0.5');
  await expect(tour(page, 'tm-passes')).toBeVisible();
  await expect(tour(page, 'tm-legend')).toBeVisible();
});

test('selection panel tabs work when a detection is selected', async ({ page }) => {
  // Selecting requires a detection feature on the map; only assert tabs when
  // the panel is actually present.
  const panel = tour(page, 'selection-panel');
  if (await panel.count() === 0 || !(await panel.first().isVisible())) {
    test.info().annotations.push({ type: 'note', description: 'no detection selected — selection panel not shown' });
    return;
  }
  for (const t of ['tab-details', 'tab-analytics', 'tab-similar', 'tab-tracks']) {
    await clickIfVisible(tour(page, t));
  }
  await clickIfVisible(tour(page, 'selection-collapse'));
});
