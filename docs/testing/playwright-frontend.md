# Frontend Tests (Playwright)

**Config:** [frontend/playwright.config.ts](../../frontend/playwright.config.ts)
**Tests:** [frontend/tests/](../../frontend/tests/)
**Visual-testing notes:** [frontend/VISUAL_TESTING.md](../../frontend/VISUAL_TESTING.md)

## Running

```bash
cd frontend
npm install
npx playwright install   # browsers (one time)
npm run test             # or `npx playwright test`
```

## What we test

- Login flow with the env-bootstrap admin
- Workspace navigation (rail + topbar state)
- Ingest upload form validation
- Map detection layer rendering
- Selection panel basic interactions

Visual regression for major workspaces — screenshots committed under `tests/screenshots/`, diffed on each run. See `VISUAL_TESTING.md` for the workflow when intentionally updating reference screenshots.

## Not tested here

Full e2e (upload → ingest → detection rendering) is too slow for CI — a benchmark concern, see [benchmark-harness.md](benchmark-harness.md).

## Cross-references

- [frontend/VISUAL_TESTING.md](../../frontend/VISUAL_TESTING.md)
- [conventions/adding-a-new-admin-tab.md](../conventions/adding-a-new-admin-tab.md) — visual testing expectations for new tabs
