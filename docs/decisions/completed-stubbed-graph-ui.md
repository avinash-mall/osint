# Completed: stubbed Link-Graph / SelectionPanel UI surfaces

**Date:** 2026-06-08
**Status:** adopted

## Decision

Close three remaining UI stubs left after the [Link Graph redesign](../architecture/link-graph-redesign.md)
Phases 1–5, replacing synthetic/placeholder rendering with real data and wiring
an orphaned component. No new backend endpoints were added — the data already
existed; the frontend was the gap.

1. **Co-occurrence bars → real temporal histogram.** The Investigation detail
   panel rendered 8 seed-hash placeholder bars (a deterministic function of the
   node id, no real meaning). Replaced with an 8-bucket histogram of the
   selected node's linked neighbours by their `created_at` across the active
   time window — derived entirely from the already-loaded graph payload, no
   extra request. Renders "No time-stamped links in window" when no neighbour
   carries a timestamp. (This is distinct from the OntologyOrbit per-object
   co-occurrence chips added in Phase 5.C, which are backed by
   `/api/graph/ontology?include_cooccurrence=true`.)

2. **Site-rollup FMV clips + reports.** `/api/graph/site-composition` already
   returned `fmv_clips` and `reports` buckets (Phase 5.L), but the frontend
   showed a stale "FMV clips + reports populate in Phase 2" note instead of
   rendering them. Added the two sections to the site-rollup detail panel.

3. **ProvenancePanel wired as the SelectionPanel "Prov" tab.**
   `ProvenancePanel.tsx` was fully built but orphan-mounted (no caller). Wired
   it as the sixth right-rail tab (`rightTab === 'provenance'`), following the
   "a tab needs three things" rule in [map-selection-panel.md](../frontend/map-selection-panel.md):
   tab-bar entry, content block, and a `setRightTab` case in GaiaMap's tour
   `onStepChange`. Added the matching `tab-provenance` product-tour step per
   CLAUDE.md hard rule #9.

## Why this design

- **Derive, don't fabricate.** The co-occurrence histogram uses real adjacency +
  timestamps the client already holds; it is honest about emptiness rather than
  always drawing bars. This follows the project's "honest 503 / honest empty"
  stance ([conventions/error-handling.md](../conventions/error-handling.md)).
- **Render existing data before adding endpoints.** Site-rollup FMV/reports and
  the provenance lineage were already computed server-side / present in the
  detection record; the minimal fix was frontend-only.

## What this touched

- `frontend/src/components/GraphExplorer.tsx`: real `cooccurrenceBars` memo +
  render; site-rollup FMV/reports sections; corrected the stale
  "Evidence + Ontology modes are stubs" comment (both modes own their own
  fetch/render paths).
- `frontend/src/components/map/SelectionPanel.tsx`: `provenance` added to
  `SelectionRightTab`, tab array, `rightHeader`, render branch; import.
- `frontend/src/components/map/ProvenancePanel.tsx`: dropped the orphan note.
- `frontend/src/components/GaiaMap.tsx`: `rightTab` union + tour mapping.
- `frontend/src/components/tour/tourSteps.ts`: `tab-provenance` step.
- `backend/routers/graph.py`: refreshed the `site-composition` docstring.

## Cross-references

- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)
- [frontend/map-selection-panel.md](../frontend/map-selection-panel.md)
- [frontend/map-review-similar-provenance.md](../frontend/map-review-similar-provenance.md)
- [backend-routers/graph-router.md](../backend-routers/graph-router.md)
- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md)
