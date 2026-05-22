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

- **Detection layer** renders `GET /api/detections/geojson` as **three stacked sub-layers**:
  1. *Icon markers* — category icons at each detection, drawn when `showDetectionCenterMarkers` is true (`visibleDetectionCount` 1–`DETECTION_CENTER_MARKER_LIMIT`, currently 800).
  2. *Dots* — plain `CircleMarker` fallback for dense scenes (`!showDetectionCenterMarkers`, i.e. count > 800).
  3. *Boxes* — one react-leaflet `<Polygon>` **per detection feature** (a `features.map(...)`, same pattern as the icon-marker layer), drawn with the map's default SVG renderer. **Always rendered** (no toggle); styled by `makeDetectionStyle` in `_helpers.ts` — solid category-coloured outline, weight 2. Geometry is converted from GeoJSON to Leaflet `[lat,lng]` arrays by `geojsonToLatLngs` in `_helpers.ts`. Clicking a box selects the detection.
  The box layer and the marker/dot layer always render together, so the analyst sees both the overview icon and the geo-truth box.
  The boxes were previously a single `<GeoJSON>` canvas layer; it silently failed to paint, so it was replaced with the per-feature `<Polygon>` map — see [decisions/why-detection-boxes-use-polygon-map.md](../decisions/why-detection-boxes-use-polygon-map.md).
- **GEOM toolbar** (top-centre of the map) switches the box shape: `HBB` (axis-aligned envelope), `OBB` (oriented rectangle, default), `MASK` (raw `geom`). State lives in `GaiaMap` as `bboxMode`.
- The legacy `showBbox` / **BBOX toggle button was removed** — see [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md).
- **Satellite pass layer** shows pass footprints (`MULTIPOLYGON`) and on click reveals the COG tile URL.
- **Time filter** comes from `TimeMachineBar`'s `(start, end)` range.
- **Cursor lat/lng** is published up to Shell via `MapEventHandlers` for the topbar readout.

## Cross-references

- [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md)
- [decisions/why-detection-boxes-use-polygon-map.md](../decisions/why-detection-boxes-use-polygon-map.md)
- [workspace-geoint-gaiamap.md](workspace-geoint-gaiamap.md)
- [map-selection-panel.md](map-selection-panel.md)
- [map-time-machine.md](map-time-machine.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
