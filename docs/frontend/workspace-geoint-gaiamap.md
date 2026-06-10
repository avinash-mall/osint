# Geoint Workspace — `GaiaMap.tsx`

**Path:** [frontend/src/components/GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx)
**Lines:** ~1830
**Depends on:** React hooks, `axios`, Leaflet/React-Leaflet components, map panels, ontology utilities, detection/imagery/analytics backend APIs

## Purpose

The Common Operating Picture: a 2D Leaflet map with all detection layers, satellite passes, asset tracks, analytics overlays, and the panels driving them.

## Why this design

`GaiaMap` owns cross-panel map state because layer visibility, selected detections, candidate links, and graph handoff all need to stay synchronized with the Leaflet viewport. Candidate-link approve/reject now sends no analyst payload; the backend derives reviewer identity from the signed session.

## Layout

```
┌────────────┬─────────────────────────────────────────┬──────────────────┐
│ LayerPanel │            MapStage (leaflet)           │ SelectionPanel   │
│            │                                         │ (Details, Analytics,│
│ filters    │ basemap + detections + tracks + passes  │ Similar, Actions)│
│ provenance │                                         │                  │
├────────────┴─────────────────────────────────────────┴──────────────────┤
│  TimeMachineBar (temporal slider)                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

## Composed of

| Sub-component | File | Doc |
|---|---|---|
| MapStage (the `<MapContainer>`) | [map/MapStage.tsx](../../frontend/src/components/map/MapStage.tsx) | [map-stage-and-layers.md](map-stage-and-layers.md) |
| LayerPanel (left rail) | [map/LayerPanel.tsx](../../frontend/src/components/map/LayerPanel.tsx) | [map-stage-and-layers.md](map-stage-and-layers.md) |
| SelectionPanel (right rail) | [map/SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx) | [map-selection-panel.md](map-selection-panel.md) |
| TimeMachineBar (footer) | [map/TimeMachineBar.tsx](../../frontend/src/components/map/TimeMachineBar.tsx) | [map-time-machine.md](map-time-machine.md) |
| ChangeDetectionDialog | [map/ChangeDetectionDialog.tsx](../../frontend/src/components/map/ChangeDetectionDialog.tsx) | [map-change-detection-dialog.md](map-change-detection-dialog.md) |
| AnalyticsToolsPanel | [map/AnalyticsToolsPanel.tsx](../../frontend/src/components/map/AnalyticsToolsPanel.tsx) | [map-analytics-tools.md](map-analytics-tools.md) |
| ReviewPanel / SimilarPanel / ProvenancePanel | [map/](../../frontend/src/components/map/) | [map-review-similar-provenance.md](map-review-similar-provenance.md) |

## Data sources

- `GET /api/detections/geojson` — live detection layer. Scoped by the current map `bbox` + time window + `limit=20000`. Each feature carries full ontology/metadata (the selection panel and OBB renderer read it), so a dense pass over a city returns thousands of features / tens of MB and can take 20–30 s; the fetch timeout is **60 s** (not 10 s — a 10 s ceiling silently dropped the whole layer on dense scenes) and the "Loading detections" spinner covers the wait.
- **Live streaming (`detections` WS topic).** The `useEventStream('detections', …)` handler branches on `message.type`: `detections_partial` (per chip) auto-selects the pass on its first chip and **appends** the chip's `features` to `detectionsGeoJSON` (deduped by `properties.id`) — so detections render within seconds, not after the whole pass. Any other message (the end-of-pass `detections_updated`) triggers the authoritative full `fetchDetections()` that reconciles the live preview with the fully-enriched set. See [decisions/why-live-streaming-detections.md](../decisions/why-live-streaming-detections.md).
- `GET /api/detections/classes?llm=true` — global Detection Classes summary; raw class keys and deterministic labels drive filtering, while `llm_advisory` can add non-authoritative operator context
- `GET /api/imagery` — satellite passes
- `GET /api/tracks/detections` — cross-image tracks
- `GET /api/geotime/features` — Bases / LaunchPoints / asset tracks
- Tile URLs for imagery from `GET /api/imagery/{id}/tiles`

## Detection rendering

`GaiaMap` owns the detection-layer view state: `bboxMode` (`hbb`/`obb`/`mask`, default `obb`) and the derived `showDetectionCenterMarkers` flag (`count` 1–`DETECTION_CENTER_MARKER_LIMIT`). Detection bounding boxes always render; no `showBbox` toggle. The **OBB** mode rebuilds the oriented polygon from `feature.properties.metadata.geo_polygon` (the backend's geo-projected flat `[lon,lat,…]` box) — it previously read `metadata.obb` (pixel-space, never matched) and silently fell back to the feature geometry. See [map-stage-and-layers.md](map-stage-and-layers.md), [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md).

The left Detection Classes list keeps `rawClass` as the hide/solo/API filter key. `displayLabel` is presentation-only; deterministic labels remain primary now that still-image YOLOE has been removed. LLM advisory text can still appear as secondary context in [LayerPanel](map-stage-and-layers.md).

The confidence slider's `confidenceThreshold` gates the sidebar as well as the map canvas. [`filteredDetectionClassStats`](../../frontend/src/components/GaiaMap.tsx#L482-L492) drops label rows whose `maxConfidence` falls below the threshold (in addition to the search filter), so a class hidden entirely from the map by confidence is also removed from the Detection Classes list; empty category/source groups then collapse via `detectionGroups`. This mirrors the canvas gate in `filteredDetectionsGeoJSON`.

## Live updates

The `imagery` SSE handler ([GaiaMap.tsx#L790-L803](../../frontend/src/components/GaiaMap.tsx#L790-L803)) refreshes `fetchImagery()` on every imagery event and, on `ingest_succeeded`, **selects the freshly-cataloged `pass_id`**. The map only draws the *selected* pass (`selectedImageryData`, `GaiaMap.tsx#L250`) and `fetchImagery()` deliberately preserves the current selection when it is still in range (`GaiaMap.tsx#L671`). Without the explicit select, the first upload showed (selection starts `null` → auto-picks `rows[0]`) but a second upload landed only in the imagery list — the map kept the first pass selected, so the new scene "processed but never appeared." Pinning the new `pass_id` makes each completed upload the displayed layer.

The `ops` SSE handler also calls `fetchData()` to refresh the static-feature/track layer — the previous `geotime` subscription was dead (the backend never publishes a `geotime` topic).

## Time-machine, change detection & overlays

`GaiaMap` owns the wiring that makes the otherwise-presentational [TimeMachineBar](map-time-machine.md) functional:

- **Playhead → imagery**: `tmPassFracs` maps each pass to a `[0,1]` position in the window; a select-nearest effect picks the pass under `tmValue` whenever the analyst scrubs or clicks a diamond.
- **Playback**: while `tmPlaying`, an interval steps `tmValue` through the passes oldest→newest (~1.2 s each), then stops.
- **Event-timeline Play = live-follow**: while `timelinePlaying`, a 5 s interval re-runs `fetchDetections()` so the density strip advances in real time.
- **Change detection**: pinning a compare pass shows a `CHANGE` button that opens [ChangeDetectionDialog](map-change-detection-dialog.md) (`changePair` state) for the active-vs-compare pair; the dialog's result is dispatched as `sentinel:overlay-geojson` and rendered by MapStage's generic overlay layer (see [map-stage-and-layers.md](map-stage-and-layers.md)).
- **Pan-on-select**: the ⌘K-jump and cross-workspace nav call `mapStageRef.panToDetection(feat)` so a programmatically-selected detection recenters the map.

## Key symbols

- [`approveCandidate`](../../frontend/src/components/GaiaMap.tsx#L1039-L1054) — approves a detection-target candidate link without sending client-side reviewer identity.
- [`rejectCandidate`](../../frontend/src/components/GaiaMap.tsx#L1056-L1070) — rejects a detection-target candidate link without sending client-side reviewer identity.
- [`fetchCandidateLinks`](../../frontend/src/components/GaiaMap.tsx#L971-L979) — refreshes candidate links after review actions.

## Inputs / Outputs

Reads detection GeoJSON, imagery pass metadata, class summaries, candidate links, and track feeds from backend APIs. Emits user actions through `axios` mutators for manual detections, review status, tags, pins, candidate-link decisions, and collection tasks.

## Failure modes

API errors are surfaced via panel/action status text while the map remains usable. Candidate-link 409s refresh through the normal candidate-link fetch path after a failed action; stale clients no longer overwrite `reviewed_by`.

## Cross-references

- [map-stage-and-layers.md](map-stage-and-layers.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md)
- [map-selection-panel.md](map-selection-panel.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
- [decisions/why-analyst-username-from-session.md](../decisions/why-analyst-username-from-session.md)
