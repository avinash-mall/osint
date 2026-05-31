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
- Detection Classes mock data covers deterministic labels plus optional LLM advisory text
- Selection panel basic interactions

Visual regression for major workspaces — screenshots committed under `tests/screenshots/`, diffed on each run. See `VISUAL_TESTING.md` for the workflow when intentionally updating reference screenshots.

## Not tested here

This tier stubs every route via `mockApi.ts`, so it cannot catch wiring/integration
bugs (auth gating, route mounting, DB/inference round-trips). Those are covered by the
**live tier** — see [live-e2e-playwright.md](live-e2e-playwright.md) (UI) and
[scripts/smoke-test-live-api.md](../scripts/smoke-test-live-api.md) (API), which drive
the real nginx:3000 stack. Full upload → ingest → detection-render remains a benchmark
concern, see [benchmark-harness.md](benchmark-harness.md).

## Cross-references

- [frontend/VISUAL_TESTING.md](../../frontend/VISUAL_TESTING.md)
- [testing/live-e2e-playwright.md](live-e2e-playwright.md) — the live-backend e2e tier this complements
- [conventions/adding-a-new-admin-tab.md](../conventions/adding-a-new-admin-tab.md) — visual testing expectations for new tabs
