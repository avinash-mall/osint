# Visual regression testing

The frontend has a small Playwright visual suite that renders real workspaces
against deterministic mocked API responses. It is meant to catch responsive
layout drift, not backend regressions.

Run:

```bash
npm run test:visual
```

Update approved baselines after an intentional UI change:

```bash
npm run test:visual:update
```

The suite currently captures:

- the unauthenticated login view on a phone-sized viewport
- the GEOINT map workspace at compact and wide widths
- the graph and admin workspaces at a medium width
- the FMV workspace at a medium width

Notes:

- Tests use the locally installed Chrome channel so they can run on this host
  even where Playwright's bundled Chromium is unavailable.
- API calls are mocked in `tests/visual/mockApi.ts`; extend that fixture when
  a workspace gains a new required endpoint.
- Screenshot baselines live beside the spec in
  `tests/visual/responsive.spec.ts-snapshots/`.
