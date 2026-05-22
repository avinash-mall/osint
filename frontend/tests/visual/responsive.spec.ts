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
  await expect(page.getByTitle('Map', { exact: true })).toBeVisible({ timeout: 10_000 });
  await stabilize(page);
}

async function expectCanScroll(page: Page, selector: string, label = selector) {
  const scroller = page.locator(selector).first();
  await expect(scroller).toBeVisible();
  await scroller.evaluate((node) => {
    const spacer = document.createElement('div');
    spacer.dataset.testid = 'overflow-spacer';
    spacer.style.blockSize = '80rem';
    spacer.style.flexShrink = '0';
    node.appendChild(spacer);
  });
  const metrics = await scroller.evaluate((node) => ({
    clientHeight: node.clientHeight,
    scrollHeight: node.scrollHeight,
  }));
  expect(metrics.scrollHeight, `${label} should overflow once content exceeds its bounds`).toBeGreaterThan(metrics.clientHeight);
  await expect
    .poll(() => scroller.evaluate((node) => {
      node.scrollTop = node.scrollHeight;
      return node.scrollTop;
    }))
    .toBeGreaterThan(0);
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

  test('login view can scroll on short screens', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 520 });
    await installMockApi(page, { authenticated: false });
    await page.goto('/');
    await expect(page.getByText('Resume operations')).toBeVisible();
    await expectCanScroll(page, '.login-screen', 'login');
  });

  test('map workspace adapts at compact and wide widths', async ({ page }) => {
    await page.setViewportSize({ width: 430, height: 900 });
    await openAuthed(page);
    // Re-dispatch until the detection data has loaded — the jump handler is a
    // one-shot listener, so a dispatch that lands before the geojson fetch
    // resolves is silently dropped (flaky under parallel dev-server load).
    await expect(async () => {
      await page.evaluate(() => window.dispatchEvent(new CustomEvent('sentinel:jump-to-detection', { detail: { id: 1 } })));
      await expect(page.getByText('Tank').first()).toBeVisible({ timeout: 1_000 });
    }).toPass({ timeout: 15_000 });
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

  test('admin tabs route vertical overflow to an intentional scroll owner', async ({ page }) => {
    await page.setViewportSize({ width: 1365, height: 900 });
    await openAuthed(page);
    await page.getByTitle('Admin').click();

    const cases = [
      ['ontology', '.ontology-admin'],
      ['processing', '.admin-jobs-list'],
      ['models', '.admin-models-table'],
      ['alerts', '.admin-alerts-list'],
      ['auth', '.admin-view'],
      ['health', '.admin-view'],
      ['confidence', '.admin-view'],
      ['prompts', '.admin-view'],
      ['versions', '.admin-view'],
    ] as const;

    for (const [tab, selector] of cases) {
      await page.evaluate((nextTab) => {
        window.dispatchEvent(new CustomEvent('sentinel:admin-tab', { detail: { tab: nextTab } }));
      }, tab);
      await expectCanScroll(page, selector, tab);
    }
  });

  test('admin count tabs do not trigger a React render loop', async ({ page }) => {
    await page.setViewportSize({ width: 1365, height: 900 });

    // The Processing/Models/Alerts views each report a row count to AdminScreen
    // via an `onCount` callback listed in a useEffect dependency array. If that
    // callback is not referentially stable, the effect re-fires every render →
    // setState → re-render → "Maximum update depth exceeded".
    const loopErrors: string[] = [];
    page.on('console', (msg) => {
      if (/maximum update depth/i.test(msg.text())) loopErrors.push(msg.text());
    });

    await openAuthed(page);
    await page.getByTitle('Admin').click();

    for (const tab of ['processing', 'models', 'alerts'] as const) {
      await page.evaluate((t) => {
        window.dispatchEvent(new CustomEvent('sentinel:admin-tab', { detail: { tab: t } }));
      }, tab);
      // Dwell long enough for a loop, if present, to trip React's depth guard
      // (~50 iterations) — empirically well under a second.
      await page.waitForTimeout(1500);
      expect(loopErrors, `${tab} tab triggered a render loop`).toEqual([]);
    }
  });

  test('upload object chooser does not trap the page above lower controls', async ({ page }) => {
    await page.setViewportSize({ width: 1365, height: 700 });
    await openAuthed(page);
    // Ingest is now a top-level workspace, not an Admin tab.
    await page.getByTitle('Ingest', { exact: true }).click();

    const tree = page.locator('.ingest-object-tree');
    await expect(tree).toBeVisible();
    await tree.evaluate((node) => {
      const spacer = document.createElement('div');
      spacer.dataset.testid = 'tall-object-tree';
      spacer.style.blockSize = '60rem';
      node.appendChild(spacer);
    });
    const treeStyle = await tree.evaluate((node) => ({
      overflowY: getComputedStyle(node).overflowY,
      maxHeight: getComputedStyle(node).maxHeight,
    }));
    expect(treeStyle).toEqual({ overflowY: 'visible', maxHeight: 'none' });

    const lowerControls = page.getByPlaceholder('one object per line, or comma separated');
    await lowerControls.scrollIntoViewIfNeeded();
    await expect(lowerControls).toBeVisible();
    await expect.poll(() => page.locator('.ingest-connect').evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  });

  test('workspace side panels expose their own scroll surfaces when canvases stay fixed', async ({ page }) => {
    await page.setViewportSize({ width: 1365, height: 700 });
    await openAuthed(page);

    await expectCanScroll(page, '.map-left-panel .sentinel-scroll', 'map left panel');
    // Re-dispatch until the detection data has loaded — the jump handler is a
    // one-shot listener, so a dispatch that lands before the geojson fetch
    // resolves is silently dropped (flaky under parallel dev-server load).
    await expect(async () => {
      await page.evaluate(() => window.dispatchEvent(new CustomEvent('sentinel:jump-to-detection', { detail: { id: 1 } })));
      await expect(page.getByText('Tank').first()).toBeVisible({ timeout: 1_000 });
    }).toPass({ timeout: 15_000 });
    await expectCanScroll(page, '.map-right-panel .sentinel-scroll', 'map right panel');

    await page.getByTitle('Link Graph').click();
    await expect(page.getByText('Link Graph · 2-hop neighborhood')).toBeVisible();
    await page.locator('.graph-entity-panel .sentinel-row').first().evaluate((node) => (node as HTMLButtonElement).click());
    await expectCanScroll(page, '.graph-entity-panel .sentinel-scroll', 'graph entity panel');
    await expectCanScroll(page, '.graph-detail-panel .sentinel-scroll', 'graph detail panel');

    await page.getByTitle('Drone Video').click();
    await expect(page.getByText('Tracks')).toBeVisible();
    await expectCanScroll(page, '.fmv-sidebar .scroll', 'fmv sidebar');
  });
});
