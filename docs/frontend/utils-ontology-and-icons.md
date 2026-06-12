# Frontend Utils — Ontology + Icons

**Paths:**
- [frontend/src/utils/apiError.ts](../../frontend/src/utils/apiError.ts) — `apiErrorMessage(err, fallback)`: normalizes axios/FastAPI errors to a renderable string (422 `detail` arrays must never reach React children — no ErrorBoundary exists)
- [frontend/src/utils/branchIcons.tsx](../../frontend/src/utils/branchIcons.tsx) — branch → icon component map
- [frontend/src/utils/defenceOntology.ts](../../frontend/src/utils/defenceOntology.ts) — `BranchId` constants and color/glyph defaults
- [frontend/src/utils/detectionTaxonomy.ts](../../frontend/src/utils/detectionTaxonomy.ts) — `CLASS_LIST` flattening, taxon → color lookup, memoized cache
- [frontend/src/utils/iconLibrary.tsx](../../frontend/src/utils/iconLibrary.tsx) — `lucide-react` re-exports plus custom threat/affiliation symbols
- [frontend/src/utils/objectMetadata.ts](../../frontend/src/utils/objectMetadata.ts) — accessors for a detection record's fields (threat scoring, provenance extraction)
- [frontend/src/utils/ontologyApi.ts](../../frontend/src/utils/ontologyApi.ts) — thin fetch wrapper over `/api/ontology/*`
- [frontend/src/utils/useOntology.ts](../../frontend/src/utils/useOntology.ts) — React hook: fetches the ontology tree, exposes `flattenBranches`, refresh on `ontology_updated` WS event
- [frontend/src/utils/uploadProgress.ts](../../frontend/src/utils/uploadProgress.ts) — `UploadJob` type and progress derivations

## Why these are shared

Every workspace touches ontology lookups (branch icon, taxon color, normalized label). Putting them in `utils/` keeps workspaces focused on their UX, and gives the ontology mapping a single live source — the React hook subscribes to ontology version changes → all consumers refresh together.

## `useOntology` watcher semantics

- Module-level cache (`_cacheBySensor`) + one 30 s version-poll interval. The watcher is armed only while at least one subscriber is live (`finally` arms only when `!cancelled`; the last unsubscribe clears it) — otherwise an unmounted hook would leave a permanent zero-subscriber poll.
- A refcount map (`_sensorRefs`) tracks subscribed sensors so a version tick re-fetches *subscribed-but-uncached* sensors too — recovers a sensor whose initial fetch failed (tree would otherwise stay `null` all session).
- `_lastVersion` is committed only when every refetch in a tick succeeded; a failed refetch retries on the next tick rather than waiting for the next version bump.
- All bare `fetch` calls here and in `ontologyApi.ts` pass `credentials: 'include'` to match the app-wide axios `withCredentials` (cross-origin `VITE_API_URL` deployments would otherwise lose the session cookie).

## Cross-references

- [backend/ontology-system.md](../backend/ontology-system.md)
- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [ontology-admin-ui.md](ontology-admin-ui.md) — primary consumer
