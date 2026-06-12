# FMV + Link Graph audit — fixes (2026-06-12)

**Date:** 2026-06-12
**Status:** adopted

## Context

A correctness audit of the FMV player and Link Graph workspaces surfaced nine
verified defects — wrong-actor graph writes, fetch races, a starved HUD timer,
a refetch storm, swallowed delete failures, a stuck transcoding overlay, and
dead-end interactions on synthetic/out-of-view nodes. All nine were confirmed
against the code (and the backend where relevant) before fixing. Fixes are
confined to [FmvPlayer.tsx](../../frontend/src/components/FmvPlayer.tsx),
[GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx) and
[EvidenceColumnDAG.tsx](../../frontend/src/components/graph/EvidenceColumnDAG.tsx).

## Fixed

**Evidence chain · Contradict wrote a self-loop**
([EvidenceColumnDAG.tsx](../../frontend/src/components/graph/EvidenceColumnDAG.tsx),
[GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx))
- The Contradict button passed the Detection leaf's own id as `actor_id`, so
  `merge_contradicted_by` ([backend/graph_writes.py](../../backend/graph_writes.py))
  MERGEd `(Detection)-[:CONTRADICTED_BY]->(Detection)` on itself instead of the
  focus-entity dissent edge. The actor is now `payload.focus.id`.
- `contradictDetection` swallowed errors with `console.error`. It now lets them
  propagate; the DAG shows a status line ("Recording dissent…" / "Contradiction
  recorded" / the server detail on failure) and on success the parent re-fetches
  `/api/graph/evidence/{focus}` so the new edge is visible immediately.

**FMV · clip-switch fetch race** — `fetchFrames`/`fetchDetections` applied their
setters unconditionally; clip A's 10 000-row KLV response could land after clip
B's and paint A's telemetry/footprint/boxes over B's video. Both now drop any
response whose clip id no longer matches `selectedIdRef` (a ref mirrored from
state on every render), which also guards the WS-handler and poller call paths.

**FMV · "dets/s" HUD always +0 while streaming** — the 1 s delta interval had
`[ndjsonTotal]` as deps, so every WS message tore it down and the tick never
fired exactly when detections were flowing. The running total now also lives in
`ndjsonTotalRef`; the interval is created once (`[]` deps) and reads refs.

**FMV · streaming refetch storm** — every `fmv_detection` /
`fmv_detections_progress` event triggered a full unbounded detections refetch
(many per second), and the Re-ID effect (deps included `detections`) refired
`GET /api/fmv/detections/{id}/similar` — which cosine-scores up to 4 000
embeddings server-side — on each one. Streaming refetches are now coalesced to
at most one per 1.5 s (`scheduleDetectionsRefetch`), with an immediate flush on
`fmv_detections_complete`; the Re-ID effect keys on `selectedDetectionId` only
and looks the anchor row up via `detectionsRef`.

**FMV · silent clip-delete failure** — `handleDeleteClip` had no catch and
`ConfirmDialog` (atoms.tsx) fires `onConfirm` without awaiting, so a failed
`DELETE /api/fmv/clips/{id}` produced an unhandled rejection while the dialog
closed as if it succeeded. The ClipsTab confirm handler now catches, surfaces a
"Delete failed: …" line in the clip library, and leaves the list untouched
(atoms.tsx deliberately not modified — owned elsewhere).

**FMV · failed clips stuck on "TRANSCODING…"** — clips marked `failed` (Celery
queueing failure, [backend/main.py](../../backend/main.py) `#L1160-L1175`) hit
an overlay and a 3 s `/api/fmv/clips` poller whose only terminal check was
`status === 'ready'`. `failed`/`error` are now terminal: the poll stops, the
overlay renders "PROCESSING FAILED — delete the clip and re-upload" instead
("processing" because `failed` covers Celery queueing failures, not just
transcode), and the clip-library status tag tones `failed` as critical.

**Graph · context menu on virtual cluster nodes** — right-clicking a Phase 5.E
`:Cluster` node (synthetic id `${parentId}:cluster:${cls}`) opened the full
menu; "Expand Node"/"Search Around" 404'd in Neo4j and blanked the canvas, and
"Evidence chain" opened an empty Evidence mode. Right-click now expands the
cluster (same as left-click) and never opens the menu for `__cluster` nodes.

**Graph · fetchData/fetchMetrics race** — rapid timeRange/classLens/mode
changes fired overlapping requests with no cancellation; the loser overwrote
the winner (30-day data under a 1H scrubber). Both fetchers now carry a
sequence ref and only the latest call commits state — equivalent to the
cancelled-flag cleanup the scope effect already used, but it also covers the
manual refresh button that shares `fetchData`.

**Graph · "Top central"/GNN clicks were silent no-ops** — `selectNodeById`
only searched the bounded feed (limit 150, time-filtered) while metrics/GNN are
whole-graph. On a miss it now fetches `POST /api/graph/neighborhood` (the
expandNode path), merges the result into the feed (deduped by node id and
source|target|predicate) and selects the node; if the fetch fails, a 3 s
notice explains the node is outside the current view.

## Why this design

- Sequence-ref guards (graph fetches) and a latest-clip-id ref (FMV fetches)
  were chosen over per-effect cancelled flags because both fetch functions are
  shared with non-effect call sites (refresh button, WS handler, pollers); a
  flag scoped to one effect would leave those paths racy.
- The contradict status line lives in `EvidenceColumnDAG` (it owns the button)
  while the HTTP + evidence refresh stays in `GraphExplorer` (it owns the
  fetch), preserving the existing parent-fetch/child-render split.
- The 1.5 s refetch throttle keeps the live-tracking feel (boxes still appear
  within ~a window stride) without the unbounded-list-per-message load.

## Cross-references

- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)
- [backend/graph-writes.md](../backend/graph-writes.md) — `merge_contradicted_by`
- [decisions/audit-fixes-ui-correctness-2026-06-08.md](audit-fixes-ui-correctness-2026-06-08.md) — the prior correctness pass
