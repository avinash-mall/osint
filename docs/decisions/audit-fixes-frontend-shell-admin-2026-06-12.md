# Frontend audit — Shell / hooks / admin / tour fixes (2026-06-12)

**Date:** 2026-06-12
**Status:** adopted

## Context

A frontend correctness audit (parallel to the worker/backend audits of the same
date) surfaced 14 verified defects in the Shell chrome, the shared hooks, the
Product Tour engine, and the admin tabs. Every finding was re-verified against
the code (and the backend contracts in `backend/routers/ws.py`,
`backend/routers/auth.py`, and `backend/worker_legacy.py#seed_reference_db`)
before fixing. All 14 were confirmed; none skipped.

## Fixed

**Product Tour** ([ProductTour.tsx](../../frontend/src/components/tour/ProductTour.tsx), [useProductTour.ts](../../frontend/src/hooks/useProductTour.ts))
- The auto-skip effect raced the parent's `onStepChange` prep: `targetExists`
  was a render-time `useMemo`, sampled before GaiaMap could open the step's
  panel, so the first step of every prep-gated group (`tab-*`, `analytics-*`,
  `tracks-*`, `tm-*`, `event-*`) was silently skipped while its panel was
  closed. `targetExists` is now tri-state state (`null` = resolving): when the
  target is absent at commit, the sampler re-queries the DOM after a
  `requestAnimationFrame` + `setTimeout(0)` (post-prep) and only then may the
  auto-skip effect advance.
- `ArrowRight` on the last step pushed `stepIndex` past the end: the overlay
  unmounted but `running` stayed true, so the global keydown handler kept
  `preventDefault`-ing arrow keys app-wide. The handler now calls `finish()`
  at the boundary, and `next()` in the hook clamps at `TOUR_STEPS.length - 1`
  as a structural backstop (the hook now imports `TOUR_STEPS`).

**Shell** ([Shell.tsx](../../frontend/src/components/Shell.tsx))
- The adaptive health poll's `reschedule()` ran unconditionally after an
  awaited tick resolved post-unmount, re-creating the interval after cleanup
  had cleared it (a permanent leaked poller per Shell mount). `reschedule()`
  now returns early when `cancelled`.

**useOntology** ([useOntology.ts](../../frontend/src/utils/useOntology.ts))
- The initial fetch's `.finally` armed the 30 s version watcher even when the
  last subscriber had already unmounted → permanent zero-subscriber poll.
  Arming now happens only when `!cancelled`.
- Watcher staleness holes: (a) `_lastVersion` was committed *before* the
  refetch, so a failed refetch marked the new version consumed and the tree
  stayed stale until the *next* bump — it now commits only after all refetches
  succeed; (b) the watcher only refetched sensors already in the cache, so a
  failed initial fetch was never recovered — a `_sensorRefs` refcount map now
  lets version ticks refetch subscribed-but-uncached sensors (and the
  subscriber callback clears the stale error on recovery).

**useEventStream** ([useEventStream.ts](../../frontend/src/hooks/useEventStream.ts))
- The reconnect loop ignored close codes: the backend rejects expired/missing
  sessions with **1008** pre-accept ([ws.py](../../backend/routers/ws.py)),
  yet the hook redialed every 3 s per topic forever. It now stops on 1008 and
  dispatches `sentinel:ws-unauthorized` on `window`; other closes use capped
  exponential backoff (3 s → 30 s, reset on successful open).

**Cross-origin credentials** ([ontologyApi.ts](../../frontend/src/utils/ontologyApi.ts), [useOntology.ts](../../frontend/src/utils/useOntology.ts))
- All five bare `fetch` calls now pass `credentials: 'include'`, matching the
  app-wide axios `withCredentials` — a cross-origin `VITE_API_URL` deployment
  otherwise dropped the session cookie on exactly these requests.

**Shared error normalizer** (new: [utils/apiError.ts](../../frontend/src/utils/apiError.ts))
- FastAPI 422 responses carry an *array of objects* in `detail`; nine admin
  views stored `err?.response?.data?.detail` straight into error state and
  rendered it as a React child — "Objects are not valid as a React child" →
  app-wide white screen (no ErrorBoundary exists). `apiErrorMessage(err,
  fallback)` (string → as-is, object → `JSON.stringify`, else `err.message`)
  is now used at every such site in
  [RepeatThresholdsView](../../frontend/src/components/admin/RepeatThresholdsView.tsx),
  [OperationalEntitiesAdmin](../../frontend/src/components/admin/OperationalEntitiesAdmin.tsx),
  [ConfOverrideView](../../frontend/src/components/admin/ConfOverrideView.tsx),
  [PromptProfilesView](../../frontend/src/components/admin/PromptProfilesView.tsx),
  [AdminAuthTab](../../frontend/src/components/AdminAuthTab.tsx),
  [ReferencePlatformsView](../../frontend/src/components/admin/ReferencePlatformsView.tsx),
  and [ObjectDetailsForm](../../frontend/src/components/ObjectDetailsForm.tsx).
  A tiny util (not a hook/component) was chosen over per-file copies because
  the same hazard recurred in 9 files.

**Admin · Reference platforms** ([ReferencePlatformsView.tsx](../../frontend/src/components/admin/ReferencePlatformsView.tsx))
- Seed button hung forever: `seedBusy` was only cleared by the WS `done`
  event, but the worker's idempotency guard returns without publishing when
  already seeded, and the `error` branch never cleared it either. Now:
  `done` with `skipped:true` (the worker publishes it as of this audit) shows
  "already seeded — use Re-seed"; `error` clears `seedBusy`; and a 10 s
  fallback timeout (armed on enqueue, disarmed by the first WS event) clears
  the busy state with a notice when no event arrives at all.
- `openPlatform` had no out-of-order guard — rapid row clicks let a slow
  earlier response overwrite the newer selection's detail (and its `finally`
  killed the newer request's loading state). A monotonic request token now
  bails on stale responses.

**Admin · Processing** ([ProcessingView.tsx](../../frontend/src/components/admin/ProcessingView.tsx))
- Running jobs rendered a real progress bar pinned at a fabricated 50 %.
  Neither jobs API exposes percent-complete, so running jobs now render an
  indeterminate striped bar; queued/done keep 0/1.

**Admin · Auth** ([AdminAuthTab.tsx](../../frontend/src/components/AdminAuthTab.tsx))
- Save showed "Service bind succeeded · ?ms" when the backend had returned
  `{ok:true, skipped:true}` (LDAP disabled / host empty —
  [auth.py](../../backend/routers/auth.py) `test-connection` short-circuit).
  The UI now branches on `test.skipped` → "Saved — bind test skipped".

**Admin · Confidence overrides** ([ConfOverrideView.tsx](../../frontend/src/components/admin/ConfOverrideView.tsx))
- The trash icon on `from_env` rows was a silent no-op (env rows are excluded
  from the save payload and rebuilt from env on reload). The button is now
  disabled for env rows with a "set via env — override the value instead"
  tooltip.

**ObjectDetailsForm** ([ObjectDetailsForm.tsx](../../frontend/src/components/ObjectDetailsForm.tsx))
- A restored sessionStorage draft (newer than the server row) set status
  `dirty` but never scheduled a save — the debounce only started in `set()`,
  so the edit silently never persisted unless the operator typed again. The
  hydrate effect now sets `dirtyRef` and schedules the debounce save with the
  merged values when the draft wins (the autosave block moved above the
  hydrate effect to keep declaration order valid).

## Cross-references

- [frontend/product-tour.md](../frontend/product-tour.md)
- [frontend/shell-and-chrome.md](../frontend/shell-and-chrome.md)
- [frontend/event-stream-hook.md](../frontend/event-stream-hook.md)
- [frontend/utils-ontology-and-icons.md](../frontend/utils-ontology-and-icons.md)
- [frontend/admin-reference-platforms.md](../frontend/admin-reference-platforms.md)
- [frontend/admin-models-and-processing.md](../frontend/admin-models-and-processing.md)
- [frontend/admin-auth-ldap.md](../frontend/admin-auth-ldap.md)
- [frontend/admin-conf-overrides.md](../frontend/admin-conf-overrides.md)
- [frontend/admin-prompt-profiles.md](../frontend/admin-prompt-profiles.md)
- [frontend/object-details-form.md](../frontend/object-details-form.md)
- [decisions/audit-fixes-ui-correctness-2026-06-08.md](audit-fixes-ui-correctness-2026-06-08.md) — the previous frontend correctness pass
