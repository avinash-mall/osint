# Live-Backend E2E (Playwright)

**Config:** [frontend/playwright.live.config.ts](../../frontend/playwright.live.config.ts)
**Tests:** [frontend/tests/e2e/](../../frontend/tests/e2e/)
**Depends on:** a running stack served at nginx `:3000`, repo-root `.env`
(`ADMIN_USERNAME`/`ADMIN_PASSWORD`), Chrome channel

## Purpose

Drives the **real** running frontend at `http://localhost:3000` (no mock API, unlike
[playwright-frontend.md](playwright-frontend.md) which stubs every route via
`tests/visual/mockApi.ts`). Logs in once for real, then walks each of the five
workspaces and clicks/asserts the interactive controls — the `data-tour` anchors in
[src/components/tour/tourSteps.ts](../../frontend/src/components/tour/tourSteps.ts) are
the canonical control checklist (CLAUDE.md rule 9), so this suite also catches
tour-vs-UI drift.

## Running

```bash
cd frontend
npm run test:e2e          # = playwright test --config playwright.live.config.ts
```

The stack must already be up; the config has **no `webServer`** (it does not start
Vite — it hits the nginx-served production bundle). 15 tests, ~18s.

## Why this design

- **Real login via a setup project.** [auth.setup.ts](../../frontend/tests/e2e/auth.setup.ts)
  POSTs `/api/auth/login` through a `request` context and saves the `sentinel_session`
  cookie to `tests/e2e/.auth/state.json` (gitignored); the `live` project depends on it
  via `storageState`, so every spec starts authenticated as admin.
- **`dispatchEvent('click')` for overlapped controls.** Map zoom/recenter/focus sit
  over the Leaflet canvas and the admin rail collapses its labels to icons — a pointer
  click lands on the wrong element. Dispatching a `click` bubbles to React's delegated
  handler regardless of overlap/visibility, so we test handler wiring, not hit-testing.
- **Welcome-modal suppression.** `gotoApp()` sets `localStorage['sentinel:tour-completed']='1'`
  before load — the first-visit Product-Tour `.confirm-overlay` otherwise intercepts
  every click. The product-tour spec opens the tour explicitly instead.
- **Graceful degradation.** Controls that only render with a selected detection
  (selection-panel tabs) or loaded overlays (geom-*/prithvi-*) are exercised with
  `clickIfVisible` and skipped-with-annotation when the live DB is sparse.

## Key symbols

- [helpers.ts](../../frontend/tests/e2e/helpers.ts) — `gotoApp`, `switchWorkspace`,
  `tour(id)` (`[data-tour]:visible` to dodge the desktop/mobile duplicate), `clickIfVisible`.
- [map.spec.ts](../../frontend/tests/e2e/map.spec.ts) — basemap selector, opacity, layer
  toggles, overlay controls, zoom/focus, draw/range-ring, product tour, time machine,
  selection panel, and the admin `imagery-delete` control (asserted present, never clicked).
- `ingest|fmv|graph|admin.spec.ts` — per-workspace render + key-control interaction;
  fmv asserts the `clip-delete` control is present.

## Inputs / Outputs

- **In:** `ADMIN_USERNAME`/`ADMIN_PASSWORD` (env or repo-root `.env`),
  `SENTINEL_BASE_URL` (default `http://localhost:3000`).
- **Out:** Playwright `list` report; traces under `test-results/` on failure.

## Failure modes

- Run via `node_modules/.bin/playwright` (what `npm run test:e2e` uses), **not `npx`** —
  `npx` hangs probing the npm registry on this air-gapped host.
- A new/renamed control without a `data-tour` anchor silently drops its tour step and
  this suite's assertion — keep `tourSteps.ts` and the steps files in sync (rule 9).
- Sparse live data → selection-panel/overlay assertions self-skip (annotation), not fail.

## Cross-references

- [decisions/why-live-test-tier.md](../decisions/why-live-test-tier.md)
- [testing/playwright-frontend.md](playwright-frontend.md) — the mock-API visual tier this complements
- [scripts/smoke-test-live-api.md](../scripts/smoke-test-live-api.md) — the API half of the live test
- [frontend/product-tour.md](../frontend/product-tour.md) — the `data-tour` anchor system
