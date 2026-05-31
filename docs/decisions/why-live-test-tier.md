# Add a live-stack test tier alongside the mock/unit tiers

**Path:** [scripts/smoke_test_api.py](../../scripts/smoke_test_api.py),
[frontend/tests/e2e/](../../frontend/tests/e2e/),
[frontend/playwright.live.config.ts](../../frontend/playwright.live.config.ts)
**Lines:** ~720 (API) + ~5 specs
**Depends on:** a running Compose stack at nginx `:3000`, env-admin creds

## Decision

Add a third test tier that runs against the **real running stack**, complementing the
two existing tiers:

| Tier | Lives in | Talks to | Catches |
|---|---|---|---|
| Backend/inference unit | `backend/tests/`, `inference-sam3/tests/` | in-process / mocked | logic regressions |
| Frontend visual | `frontend/tests/visual/` + `mockApi.ts` | a **stubbed** API | render/layout regressions |
| **Live (new)** | `scripts/smoke_test_api.py`, `frontend/tests/e2e/` | the **real** nginx:3000 | wiring/integration: auth gating, route mounting, DB/inference round-trips, tour-vs-UI drift |

The API half exercises 105–108 of 152 routes; the UI half walks all five workspaces
clicking the `data-tour` controls. Both are committed and repeatable (`make`-free):
`python scripts/smoke_test_api.py` and `npm run test:e2e`.

## Why this design

The mock tier proves a component renders given a fixed payload; it cannot prove the
backend actually returns that payload, that the session middleware gates the right
verbs, or that `/api/inference/load` even reaches the GPU service. Those are exactly
the failures that matter on a self-contained air-gapped appliance, and they only
surface against the live stack. Driving real HTTP + a real browser session is the only
way to test the seams between the ~14 services.

Three constraints shaped the implementation, learned by breaking the live system once:

1. **`/openapi.json` is not host-reachable** (nginx serves the SPA shell for it), so the
   route catalog is generated from the spec inside the backend container and embedded.
2. **Unloading models on a live system corrupts in-flight detections** and load/unload
   409s while busy — so the inference test is idle-gated and always reloads a baseline.
3. **The FMV video pipeline monopolises the single inference service for minutes** —
   so the heavy clip upload is opt-in (`--fmv`), keeping default runs fast and safe.

## Considered alternatives

- **Extend the mock visual suite to "cover" the API.** Rejected: mocks can never catch
  integration/wiring bugs — the whole point of this tier.
- **Blindly iterate every OpenAPI route asserting `<500`.** Rejected: meaningless for
  path-param and stateful endpoints, and gives no real exercise of mutating flows;
  curated fixture-resolving flows + coverage scoring are honest instead.
- **Test the inference profile by leaving it switched.** Rejected: that is the bug that
  broke the live service — capture/restore the baseline and idle-gate instead.

## Cross-references

- [scripts/smoke-test-live-api.md](../scripts/smoke-test-live-api.md)
- [testing/live-e2e-playwright.md](../testing/live-e2e-playwright.md)
- [testing/playwright-frontend.md](../testing/playwright-frontend.md) — the mock tier this complements
- [architecture/system-overview.md](../architecture/system-overview.md) — the service seams under test
