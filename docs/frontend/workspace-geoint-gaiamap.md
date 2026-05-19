# Geoint Workspace — `GaiaMap.tsx`

**Path:** [frontend/src/components/GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx)
**Lines:** ~70085 characters (~2000 lines TSX)

## Purpose

The Common Operating Picture: a 2D Leaflet map with all detection layers, satellite passes, asset tracks, analytics overlays, and the panels that drive them.

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
| MapStage (the actual `<MapContainer>`) | [map/MapStage.tsx](../../frontend/src/components/map/MapStage.tsx) | [map-stage-and-layers.md](map-stage-and-layers.md) |
| LayerPanel (left rail) | [map/LayerPanel.tsx](../../frontend/src/components/map/LayerPanel.tsx) | [map-stage-and-layers.md](map-stage-and-layers.md) |
| SelectionPanel (right rail) | [map/SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx) | [map-selection-panel.md](map-selection-panel.md) |
| TimeMachineBar (footer) | [map/TimeMachineBar.tsx](../../frontend/src/components/map/TimeMachineBar.tsx) | [map-time-machine.md](map-time-machine.md) |
| ChangeDetectionDialog | [map/ChangeDetectionDialog.tsx](../../frontend/src/components/map/ChangeDetectionDialog.tsx) | [map-change-detection-dialog.md](map-change-detection-dialog.md) |
| AnalyticsToolsPanel | [map/AnalyticsToolsPanel.tsx](../../frontend/src/components/map/AnalyticsToolsPanel.tsx) | [map-analytics-tools.md](map-analytics-tools.md) |
| ReviewPanel / SimilarPanel / ProvenancePanel | [map/](../../frontend/src/components/map/) | [map-review-similar-provenance.md](map-review-similar-provenance.md) |

## Data sources

- `GET /api/detections/geojson` for the live detection layer
- `GET /api/imagery` for satellite passes
- `GET /api/tracks/detections` for cross-image tracks
- `GET /api/geotime/features` for Bases / LaunchPoints / asset tracks
- Tile URLs for imagery from `GET /api/imagery/{id}/tiles`

## Cross-references

- [map-stage-and-layers.md](map-stage-and-layers.md)
- [map-selection-panel.md](map-selection-panel.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
