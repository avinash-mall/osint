# Map Stage + LayerPanel

**Paths:**
- [frontend/src/components/map/MapStage.tsx](../../frontend/src/components/map/MapStage.tsx) (~33720 chars)
- [frontend/src/components/map/LayerPanel.tsx](../../frontend/src/components/map/LayerPanel.tsx) (~19277 chars)
- [frontend/src/components/map/MapEventHandlers.tsx](../../frontend/src/components/map/MapEventHandlers.tsx)
- [frontend/src/components/map/_helpers.ts](../../frontend/src/components/map/_helpers.ts) (projection/bounds/GeoJSON transforms)
- [frontend/src/components/map/_icons.tsx](../../frontend/src/components/map/_icons.tsx) (detection-type icon factory)

## Purpose

`MapStage` is the `<MapContainer>` with the offline Carto Dark basemap + every overlay layer (detections, satellite passes, asset tracks, analytics polygons, sensor footprints). `LayerPanel` is the left rail that toggles layer visibility, sets confidence filters, and shows provenance.

## Key behaviors

- **Detection layer** is a GeoJSON renderer for `GET /api/detections/geojson` with style by `parent_class` and confidence band.
- **Satellite pass layer** shows pass footprints (`MULTIPOLYGON`) and on click reveals the COG tile URL.
- **Time filter** comes from `TimeMachineBar`'s `(start, end)` range.
- **Cursor lat/lng** is published up to Shell via `MapEventHandlers` for the topbar readout.

## Cross-references

- [workspace-geoint-gaiamap.md](workspace-geoint-gaiamap.md)
- [map-selection-panel.md](map-selection-panel.md)
- [map-time-machine.md](map-time-machine.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
