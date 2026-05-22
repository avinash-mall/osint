# Map Stage + LayerPanel

**Paths:**
- [frontend/src/components/map/MapStage.tsx](../../frontend/src/components/map/MapStage.tsx) (~33720 chars)
- [frontend/src/components/map/LayerPanel.tsx](../../frontend/src/components/map/LayerPanel.tsx) (~19277 chars)
- [frontend/src/components/map/MapEventHandlers.tsx](../../frontend/src/components/map/MapEventHandlers.tsx)
- [frontend/src/components/map/_helpers.ts](../../frontend/src/components/map/_helpers.ts) (projection/bounds/GeoJSON transforms)
- [frontend/src/components/map/_icons.tsx](../../frontend/src/components/map/_icons.tsx) (detection-type icon factory)

## Purpose

`MapStage` = the `<MapContainer>` with the offline Carto Dark basemap + every overlay layer (detections, satellite passes, asset tracks, analytics polygons, sensor footprints). `LayerPanel` = the left rail toggling layer visibility, setting confidence filters, showing provenance.

## Key behaviors

- **Detection layer** renders `GET /api/detections/geojson` as **three stacked sub-layers**:
  1. *Icon markers* — category icons at each detection, drawn when `showDetectionCenterMarkers` true (`visibleDetectionCount` 1–`DETECTION_CENTER_MARKER_LIMIT`, currently 800).
  2. *Dots* — plain `CircleMarker` fallback for dense scenes (`!showDetectionCenterMarkers`, i.e. count > 800).
  3. *Boxes* — one react-leaflet `<Polygon>` **per detection feature** (a `features.map(...)`, same pattern as the icon-marker layer), drawn with the map's default SVG renderer. **Always rendered** (no toggle); styled by `makeDetectionStyle` in `_helpers.ts` — solid category-coloured outline, weight 2. Geometry converted GeoJSON → Leaflet `[lat,lng]` arrays by `geojsonToLatLngs` in `_helpers.ts`. Clicking a box selects the detection.
  Box layer + marker/dot layer always render together → analyst sees both the overview icon and the geo-truth box.
  Boxes were previously a single `<GeoJSON>` canvas layer; it silently failed to paint → replaced with the per-feature `<Polygon>` map — see [decisions/why-detection-boxes-use-polygon-map.md](../decisions/why-detection-boxes-use-polygon-map.md).
- **GEOM toolbar** (top-centre) switches box shape: `HBB` (axis-aligned envelope), `OBB` (oriented rectangle, default), `MASK` (raw `geom`). State in `GaiaMap` as `bboxMode`.
- Legacy `showBbox` / **BBOX toggle button removed** — see [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md).
- **Satellite pass layer** shows pass footprints (`MULTIPOLYGON`), on click reveals the COG tile URL. The SAT `TileLayer` proxies TiTiler COG tiles via `/tiles/`, tuned for smooth zoom:
  - `maxNativeZoom={selectedImageryData.native_max_zoom ?? 18}` — caps upstream fetches at the COG's true pixel resolution (field from `GET /api/imagery`); past it Leaflet upscales the cached tile client-side instead of round-tripping TiTiler for upsampled tiles.
  - `maxZoom={22}` — layer still interactive past native zoom (client-side upscale).
  - `keepBuffer={6}` — wider ring of tiles alive across pan/zoom → no bare tiles mid-gesture.
  - `updateWhenZooming={false}` — skips intermediate-zoom requests during a gesture.
  - tile URL uses the `.webp` format extension (~5× smaller than PNG for 3-band RGB).
  See [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md).
- **Time filter** comes from `TimeMachineBar`'s `(start, end)` range.
- **Cursor lat/lng** published up to Shell via `MapEventHandlers` for the topbar readout.
- **Focus mode** (UX-AUDIT F12) — `F` (or the eye button in the zoom cluster) collapses floating map chrome to the viewport edges via `.map-focus-on` / `.map-focus-collapsible` classes, leaving a 24 px hover lip. Floating zoom controls are 32×32 px, wired to the live Leaflet instance, with keyboard hints in tooltips (F14).

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
