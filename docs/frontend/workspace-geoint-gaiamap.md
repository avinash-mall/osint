# Geoint Workspace — `GaiaMap.tsx`

**Path:** [frontend/src/components/GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx)
**Lines:** ~1670

## Purpose

The Common Operating Picture: a 2D Leaflet map with all detection layers, satellite passes, asset tracks, analytics overlays, and the panels driving them.

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

- `GET /api/detections/geojson` — live detection layer
- `GET /api/detections/classes?llm=true` — global Detection Classes summary; raw class keys drive filtering, while `display_label` may show an LLM advisory for all-YOLOE-PF imagery AMG rows
- `GET /api/imagery` — satellite passes
- `GET /api/tracks/detections` — cross-image tracks
- `GET /api/geotime/features` — Bases / LaunchPoints / asset tracks
- Tile URLs for imagery from `GET /api/imagery/{id}/tiles`

## Detection rendering

`GaiaMap` owns the detection-layer view state: `bboxMode` (`hbb`/`obb`/`mask`, default `obb`) and the derived `showDetectionCenterMarkers` flag (`count` 1–`DETECTION_CENTER_MARKER_LIMIT`). Detection bounding boxes always render; no `showBbox` toggle. See [map-stage-and-layers.md](map-stage-and-layers.md), [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md).

The left Detection Classes list keeps `rawClass` as the hide/solo/API filter key. `displayLabel` is used only for presentation: when the backend marks `label_source="llm_advisory"` for image `yolo26+amg` rows, the LLM label is primary and the raw class remains visible in [LayerPanel](map-stage-and-layers.md). See [decisions/why-amg-detection-classes-use-llm-labels.md](../decisions/why-amg-detection-classes-use-llm-labels.md).

## Cross-references

- [map-stage-and-layers.md](map-stage-and-layers.md)
- [decisions/why-amg-detection-classes-use-llm-labels.md](../decisions/why-amg-detection-classes-use-llm-labels.md)
- [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md)
- [map-selection-panel.md](map-selection-panel.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
