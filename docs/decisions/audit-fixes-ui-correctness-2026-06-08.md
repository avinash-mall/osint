# UI correctness audit — fixes (2026-06-08)

**Date:** 2026-06-08
**Status:** adopted

## Context

A page-by-page interactivity audit (6 parallel reviewers over Map, Admin, FMV,
Link Graph, Ingest/Ontology, Shell/hooks) surfaced correctness defects beyond
the no-op-button class fixed earlier. Each finding was verified against the
actual code/backend before fixing (several agent claims were false positives —
e.g. the FMV fullscreen/export wiring, the cluster-collapse logic, and the
`API_BASE_URL` const were all correct). Confirmed, low-risk fixes below.

## Fixed

**Admin · Processing** ([ProcessingView.tsx](../../frontend/src/components/admin/ProcessingView.tsx))
- Analytics jobs are stored with `status='complete'`; the view only matched
  `'completed'`/`'done'`, so completed jobs never showed DONE (progress stuck at
  0%, grey status, hidden by the DONE filter). Added an `isDoneStatus` helper
  recognising all three.
- Training job titles read `j.dataset_name` (column is `name`) → every job
  showed `training:<id>`. Fixed to `j.name`.
- The Map/FMV cross-nav buttons read `j.output?.detection_id` — analytics jobs
  return a `result` (a GeoJSON FeatureCollection), never `output`/`detection_id`,
  and training jobs carry neither. The buttons could never render with real data,
  so they (and the dead `detection_id`/`fmv_clip_id` plumbing + the props) were
  removed.

**Admin · Confidence overrides** ([ConfOverrideView.tsx](../../frontend/src/components/admin/ConfOverrideView.tsx))
- The per-class "BASE" column showed the global env floor on every row and used
  it as the "raised above base" pivot. Now each `Row` carries its own `base`
  (`env_per_class_confidence_overrides[id]`, falling back to the global floor),
  shown and compared per-class.

**Admin · Operational entities** ([OperationalEntitiesAdmin.tsx](../../frontend/src/components/admin/OperationalEntitiesAdmin.tsx))
- `asset` entities were counted but never listed (the `KINDS` array omitted
  them) and absent from the filter, though the backend accepts the kind. Added
  `asset` (Boxes icon).

**Link Graph** ([GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx), [OntologyOrbit.tsx](../../frontend/src/components/graph/OntologyOrbit.tsx))
- Ontology-triage "Existing object" dropdown was always empty and sub-branches
  were missing: the loader read a flat `tree.objects`, but `GET /api/ontology`
  returns a nested `{branches: roots}` tree (each branch holds `.children` +
  `.objects`). Now walks the tree to flatten all branches + objects.
- Edge width and the score tooltip read `link.score`, but the serialiser puts
  relationship props under `link.properties`. Added `linkScore()` reading
  `link.properties.score` so weighted edges + the detail-panel strength bars
  reflect the real candidate-link score.
- The Investigation stats/legend overlay rendered on top of the Evidence DAG
  (showing `0/0/0.00`). Gated it to `mode === 'investigation'`.
- Switching modes via the tab strip left a stale evidence chain / selection;
  the handler now clears `evidencePayload`/`evidenceFocusId`/`selectedNode`/
  `contextMenu`.

**FMV player** ([FmvPlayer.tsx](../../frontend/src/components/FmvPlayer.tsx))
- ←/→/J/L frame steps didn't `preventDefault()`, so the keys also scrolled the
  page. Added it (the input-focus guard already protected text fields).
- The "Detections · IN FRAME" filter used a symmetric window (counted *future*
  frames), disagreeing with the backward-only canvas overlay. Made it
  backward-only so list and overlay agree.
- The playhead used bare `duration` (0 until `loadedmetadata`); now falls back
  to the clip's `duration_seconds`.

**Map** ([GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx))
- `useEventStream('geotime', …)` subscribed to a topic the backend never
  publishes, so the static-feature/track layer never live-refreshed. Folded the
  reload into the real `ops` handler and removed the dead subscription.
- Selecting a detection via the ⌘K jump or cross-workspace nav set the selection
  but never recentred the map (the cross-nav comment even promised "fit the map
  to it"). Both now call `mapStageRef.panToDetection(feat)`.

**Shell / auth** ([Shell.tsx](../../frontend/src/components/Shell.tsx), [useAuth.ts](../../frontend/src/hooks/useAuth.ts), [branchIcons.tsx](../../frontend/src/utils/branchIcons.tsx))
- ⌘K "Jump to DET-####" and the health-alert bell dispatched their CustomEvent
  synchronously after `onNavigate`, before the target workspace mounted its
  listener — so from another workspace the jump/tab-switch was dropped. Deferred
  the dispatch a tick.
- The analyst preferences menu only closed on mouse-leave (stranded for
  keyboard/touch). Added Escape + outside-click.
- The legacy `branchIcons` matcher returned the generic `Shield` for warships
  (destroyer/frigate/cruiser/carrier/submarine…), indistinguishable from armor;
  now returns `Ship`, matching `iconLibrary` and the `naval` key.
- A transient network/5xx on the boot `GET /api/auth/me` probe surfaced a
  hostile "login failed" banner though no login was attempted; the probe now
  drops to the login screen silently.

**Ingest** ([IngestConnect.tsx](../../frontend/src/components/IngestConnect.tsx))
- `activeJob` matched by `upload_id` regardless of media type, so toggling
  imagery↔FMV showed the prior media's job; the id-match now also requires
  `media_type === mediaType`.
- `selectedPrompts` omitted `defenceObjectById` from its memo deps
  (stale-closure on sensor switch); added it.

## Deferred (reported, not fixed — larger features or product decisions)

These are genuine gaps but require backend work, new UI, or design decisions, so
they were not patched in this correctness pass:

- **OBB box mode** renders the mask instead of an oriented box: the frontend
  expects geo `[lon,lat]` pairs but the backend stores a flat *normalized*
  8-float OBB. A correct fix needs the backend to emit a geo-projected OBB. The
  mask polygon shown meanwhile is an accurate detection outline, so this is a
  degraded feature, not wrong data.
- **Time-machine scrubber/Play** and the **event-timeline Play** button are
  presentational only (state isn't wired to imagery/detection filtering or an
  animation loop).
- **ChangeDetectionDialog** is never mounted (and its `sentinel:overlay-geojson`
  handoff has no map listener).
- **Viewshed/LOS** lack target-height inputs (the params are sent as defaults).
- **OntologyAdmin "Recent instances"** under-reports: it matches an id-derived
  string against an exact, case-sensitive backend `d.class`; the correct match
  key (raw class vs ontology label/prompt) needs design.

## Cross-references

- [frontend/admin-models-and-processing.md](../frontend/admin-models-and-processing.md)
- [frontend/admin-conf-overrides.md](../frontend/admin-conf-overrides.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)
- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
- [frontend/shell-and-chrome.md](../frontend/shell-and-chrome.md)
