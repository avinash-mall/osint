# Map Stage + LayerPanel

**Paths:**
- [frontend/src/components/map/MapStage.tsx](../../frontend/src/components/map/MapStage.tsx) (~38280 chars)
- [frontend/src/components/map/LayerPanel.tsx](../../frontend/src/components/map/LayerPanel.tsx) (~587 lines)
- [frontend/src/components/map/MapEventHandlers.tsx](../../frontend/src/components/map/MapEventHandlers.tsx)
- [frontend/src/components/map/_helpers.ts](../../frontend/src/components/map/_helpers.ts) (~396 lines; projection/bounds/GeoJSON transforms + class-stat shape + Task 1.2 `displayLabel` / `labelQuality`)
- [frontend/src/components/map/_icons.tsx](../../frontend/src/components/map/_icons.tsx) (detection-type icon factory + `BasemapThumb` previews)

## Purpose

`MapStage` = the `<MapContainer>` with the offline Carto Dark basemap + every overlay layer (detections, satellite passes, asset tracks, analytics polygons, sensor footprints). `LayerPanel` = the left rail toggling layer visibility, setting confidence filters, showing provenance.

The basemap selector is a **thumbnail gallery** — three 56×40 hand-painted `BasemapThumb` SVGs (`_icons.tsx`) for the dark-vector / satellite / hillshade options, active tile outlined in `--accent-cool` with a check chip. Overlay rows carry **no eye-toggle column**: a 10 px coloured dot is the visibility signal (filled = on, hollow = off), and `viewshed` / `los` / `routes` sit in a separate "Analytics tools" subgroup with a lock glyph until their tool is run. See [decisions/why-layerpanel-dot-toggle.md](../decisions/why-layerpanel-dot-toggle.md).

## Key behaviors

- **Detection layer** renders `GET /api/detections/geojson` as **three stacked sub-layers**:
  1. *Icon markers* — category icons at each detection, drawn when `showDetectionCenterMarkers` true (`visibleDetectionCount` 1–`DETECTION_CENTER_MARKER_LIMIT`, currently 800).
  2. *Dots* — plain `CircleMarker` fallback for dense scenes (`!showDetectionCenterMarkers`, i.e. count > 800).
  3. *Boxes* — one react-leaflet `<Polygon>` **per detection feature** (a `features.map(...)`, same pattern as the icon-marker layer), drawn with the map's default SVG renderer. **Always rendered** (no toggle); styled by `makeDetectionStyle` in `_helpers.ts` — solid category-coloured outline, weight 2. Geometry converted GeoJSON → Leaflet `[lat,lng]` arrays by `geojsonToLatLngs` in `_helpers.ts`. Clicking a box selects the detection.
  Box layer + marker/dot layer always render together → analyst sees both the overview icon and the geo-truth box.
  Boxes were previously a single `<GeoJSON>` canvas layer; it silently failed to paint → replaced with the per-feature `<Polygon>` map — see [decisions/why-detection-boxes-use-polygon-map.md](../decisions/why-detection-boxes-use-polygon-map.md).
- **Box mode** segmented control switches detection box shape: `HBB` (axis-aligned envelope), `OBB` (oriented rectangle, default), `MASK` (raw `geom`). State in `GaiaMap` as `bboxMode`. Renders inside the LayerPanel **Overlays** section (between Layer toggles and Analytics tools) — relocated from the MapStage top-centre toolbar so all layer-display state lives in one rail. See [decisions/why-geom-prithvi-in-layerpanel.md](../decisions/why-geom-prithvi-in-layerpanel.md).
- **Prithvi overlays** (Flood / Burn / Crops) — three independent `OverlayRow` toggles in the LayerPanel **Overlays** section, drive the `prithviOverlays` map in `GaiaMap` and the `<TileLayer>` mounts in MapStage. Also relocated from the top-centre toolbar.
- **Detection Classes display labels** — rows keep `rawClass` as the hide/solo/filter key and deterministic labels as primary. Optional LLM advisory text can render as a secondary pill, but still-image YOLOE no longer promotes generated labels. See [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md).
- **Top-centre action bar** — single horizontal row with three command buttons: **Draw object** · **Range ring** · **Product Tour**. The legacy GEOM/PRITHVI/tracks pill has been removed; tracks visibility lives in LayerPanel's "Active Tracks" row.
- Legacy `showBbox` / **BBOX toggle button removed** — see [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md).
- **Generic GeoJSON overlay subsystem** — `MapStage` keeps an `overlays` list and listens for the `sentinel:overlay-geojson` window event (`{id, label, featureCollection}`); it renders each as a `<GeoJSON>` layer (magenta, fill opacity scaled by `score`/`confidence`), flies to its bounds, and shows a dismissible chip. `sentinel:overlay-clear` (or the chip) removes them. This is how [ChangeDetectionDialog](map-change-detection-dialog.md)'s "Open on map" result reaches the workspace; any feature can reuse the event. (The dialog dispatched the event before, but no listener existed.)
- **Recenter (0-key / button)** fits the selected imagery footprint, then the current detections, then the default view — no longer a hardcoded Gulf jump. `panToDetection(feature)` on the imperative handle flies to a feature's bounds (used by ⌘K-jump / cross-nav).
- **Basemap composition** — the SAT / BASE / TERRAIN picker composes an ordered, `zIndex`-explicit `TileLayer` stack: the COG imagery is the analyst's ground truth and renders at the bottom (`zIndex={200}`, full opacity) whenever a scene is loaded, in *every* mode; BASE/TERRAIN add the Carto/Terrain basemap as a **reference overlay on top** (`zIndex={300}`, opacity from the LayerPanel slider); a cartographic fallback (`zIndex={100}`) renders only when no imagery is loaded so the stage is never empty. SAT mode = imagery alone. The old `lastNonSatBaseRef` "remember last non-SAT base" workaround was removed — it kept a basemap rendered *under* the imagery and hid the imagery in BASE/TERRAIN. The LayerPanel opacity slider label reads `IMAGERY` (disabled in SAT mode) or `<MODE> OVERLAY`, and keys only on `base`/`terrain` — SAT imagery always renders at full opacity. The reference overlay also **unmounts above z=14** (the offline bake ceiling, `BASEMAP_OVERLAY_MAX_ZOOM`) — the panel surfaces a `Reference hidden past zoom 14 · imagery only` hint and disables the opacity slider. See [decisions/why-basemap-overlay-composition.md](../decisions/why-basemap-overlay-composition.md) and [decisions/why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md).
- **Satellite pass layer** shows pass footprints (`MULTIPOLYGON`), on click reveals the COG tile URL. The SAT `TileLayer` proxies TiTiler COG tiles via `/tiles/`, tuned for smooth zoom:
  - `maxNativeZoom={selectedImageryData.native_max_zoom ?? 18}` — caps upstream fetches at the COG's true pixel resolution (field from `GET /api/imagery`); past it Leaflet upscales the cached tile client-side instead of round-tripping TiTiler for upsampled tiles.
  - `maxZoom={22}` — layer still interactive past native zoom (client-side upscale).
  - `keepBuffer={6}` — wider ring of tiles alive across pan/zoom → no bare tiles mid-gesture.
  - `updateWhenZooming={false}` — skips intermediate-zoom requests during a gesture.
  - tile URL uses the `.webp` format extension (~5× smaller than PNG for 3-band RGB).
  See [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md).
- **Time filter** comes from `TimeMachineBar`'s `(start, end)` range.
- **Cursor lat/lng** published up to Shell via `MapEventHandlers` for the topbar readout. The bottom-left cursor chip also renders a live MGRS string via `mgrs.forward([cursor.lon, cursor.lat], 5)` (was previously a hardcoded "MGRS AUTO" placeholder) — see [decisions/live-mgrs-cursor-readout.md](../decisions/live-mgrs-cursor-readout.md).
- **Manual detection class entry** — drawing a rectangle in Draw mode now opens an in-app `ManualDetectionDialog` (themed, focus-trapped) instead of `window.prompt`, because hardened defense browser profiles block native prompts; see [decisions/manual-draw-modal-replaces-prompt.md](../decisions/manual-draw-modal-replaces-prompt.md).
- **Reference overlays in LayerPanel** are now split into two toggles:
  - `borders` — admin/country GeoJSON (the layer formerly mislabeled "Tactical Grid"), rendered by `MapStage` when `activeLayers.borders` is true.
  - `graticule` — a true coordinate graticule rendered by `MgrsGraticule.tsx`: degree lines at low zoom that switch to MGRS-aligned grid bands at higher zoom. Pure-Leaflet, uses the existing `mgrs` package; no new dependency. See [decisions/borders-vs-graticule-split.md](../decisions/borders-vs-graticule-split.md).
- **Range rings** — a toolbar button activates one-shot click pick mode; the picked center opens `RangeRingsDialog` for comma-separated radii (km). The map renders one `Circle` per radius and a center dot whose right-click removes the ring set. Session state only — not persisted.
- **Area dossier (Tier C)** — right-clicking anywhere on the map (`MapContextHandler` in `MapEventHandlers.tsx`) fetches `GET /api/dossier` and opens a Leaflet popup with the country (offline point-in-polygon over `ne_countries`: name/ISO3/pop/GDP) and the count of detections within 25 km. Fully offline; see [decisions/why-offline-area-dossier.md](../decisions/why-offline-area-dossier.md).
- **Side-by-side imagery compare** — when GaiaMap supplies a `compareImagery` pass, `SwipeControl.tsx` mounts a second TileLayer in a custom `sentinel-compare` Leaflet pane and clips it with CSS `clip-path: inset(0 0 0 N%)` driven by a draggable divider chip. No external plugin — keeps the workstation offline-safe.
- **LOS obstruction tooltips** — obstruction features are rendered as individual `CircleMarker`s (Point per obstruction), each carrying a sticky tooltip with `ELEV`, `BLOCKED clearance`, `distance` from observer.
- **Detection popup label-quality row (Task 1.2)** — the marker popup that opens on icon-click now reads its title via `displayLabel(p)` from `_helpers.ts`, so DOTA-OBB generic detections surface as `"Aircraft (generic)"` instead of a fabricated specific defence label. A new monospace `LABEL_QUALITY {verified|generic|inferred}` row sits between `ORIG` and `CONF`. See [decisions/why-generic-labels-when-unverified.md](../decisions/why-generic-labels-when-unverified.md).
- **Tactical visual modes** (B2) — a palette button in the top-right zoom cluster cycles a cosmetic CSS filter over the Leaflet tile/overlay panes: `DEFAULT → FLIR (thermal) → NVG (night-vision green) → CRT (retro phosphor + scanline veil)`. State is `visualMode` in MapStage; the wrapper carries `map-vmode-<mode>` and the filters live in `index.css` under `.map-vmode-*`. Cosmetic only — no data, no network; the floating chrome and popups stay legible because the filter targets `.leaflet-tile-pane` / `.leaflet-overlay-pane`, not the whole stage. Tour anchor `visual-mode`.
- **Focus mode** (UX-AUDIT F12) — `F` (or the eye button in the zoom cluster) collapses floating map chrome to the viewport edges via `.map-focus-on` / `.map-focus-collapsible` classes, leaving a 24 px hover lip. The **zoom / recenter / focus cluster** (F14) — four 32×32 px buttons wired to the live Leaflet instance — sits at the **viewport's bottom-right corner** (`bottom: 14, right: 4`), flush against the SelectionPanel's right edge and overlapping its rightmost ~18 px strip (panel right-margin is 14 px; cluster width 32 px). `z-[600]` keeps it above the panel (`zIndex: 500`). Position is static — when the panel collapses to a 36 px rail the cluster covers the rail's left ~22 px while the remaining ~14 px on the right stays clickable for re-expand.

## Cross-references

- [decisions/why-bbox-toggle-removed.md](../decisions/why-bbox-toggle-removed.md)
- [decisions/why-generic-labels-when-unverified.md](../decisions/why-generic-labels-when-unverified.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [decisions/live-mgrs-cursor-readout.md](../decisions/live-mgrs-cursor-readout.md)
- [decisions/manual-draw-modal-replaces-prompt.md](../decisions/manual-draw-modal-replaces-prompt.md)
- [decisions/borders-vs-graticule-split.md](../decisions/borders-vs-graticule-split.md)
- [decisions/temporal-swipe-comparator.md](../decisions/temporal-swipe-comparator.md)
- [decisions/los-obstruction-point-features.md](../decisions/los-obstruction-point-features.md)
- [decisions/why-basemap-overlay-composition.md](../decisions/why-basemap-overlay-composition.md)
- [decisions/why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)
- [decisions/why-layerpanel-dot-toggle.md](../decisions/why-layerpanel-dot-toggle.md)
- [decisions/why-detection-boxes-use-polygon-map.md](../decisions/why-detection-boxes-use-polygon-map.md)
- [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md)
- [decisions/why-custom-tour-engine.md](../decisions/why-custom-tour-engine.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md)
- [product-tour.md](product-tour.md) — Product Tour button lives in the top-center toolbar (alongside Draw object / Range ring)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
- [workspace-geoint-gaiamap.md](workspace-geoint-gaiamap.md)
- [map-selection-panel.md](map-selection-panel.md)
- [map-time-machine.md](map-time-machine.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
