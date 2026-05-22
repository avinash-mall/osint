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
- **Satellite pass layer** shows pass footprints (`MULTIPOLYGON`) and on click reveals the COG tile URL. The SAT `TileLayer` proxies TiTiler COG tiles via `/tiles/` and is tuned for smooth zoom:
  - `maxNativeZoom={selectedImageryData.native_max_zoom ?? 18}` — caps upstream fetches at the COG's true pixel resolution (field from `GET /api/imagery`); past it Leaflet upscales the cached tile client-side instead of round-tripping TiTiler for upsampled tiles.
  - `maxZoom={22}` — the layer is still interactive past native zoom (client-side upscale).
  - `keepBuffer={6}` — keeps a wider ring of tiles alive across pan/zoom so the map doesn't degrade to bare tiles mid-gesture.
  - `updateWhenZooming={false}` — skips intermediate-zoom requests during a gesture.
  - tile URL uses the `.webp` format extension (~5× smaller than PNG for 3-band RGB).
  See [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md).
- **Time filter** comes from `TimeMachineBar`'s `(start, end)` range.
- **Cursor lat/lng** is published up to Shell via `MapEventHandlers` for the topbar readout.
- **Focus mode** (UX-AUDIT F12) — `F` (or the eye button in the zoom cluster) collapses the floating map chrome to the viewport edges via the `.map-focus-on` / `.map-focus-collapsible` classes, leaving a 24 px hover lip. The floating zoom controls are 32×32 px, wired to the live Leaflet instance, with keyboard hints in their tooltips (F14).

## Cross-references

- [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md)
- [decisions/why-detection-boxes-use-polygon-map.md](../decisions/why-detection-boxes-use-polygon-map.md)
- [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
- [workspace-geoint-gaiamap.md](workspace-geoint-gaiamap.md)
- [map-selection-panel.md](map-selection-panel.md)
- [map-time-machine.md](map-time-machine.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
