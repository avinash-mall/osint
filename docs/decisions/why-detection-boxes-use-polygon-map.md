# Detection Boxes Render As Per-Feature `<Polygon>`, Not A `<GeoJSON>` Layer

## Decision

**Removed:** the single react-leaflet `<GeoJSON>` canvas layer that drew all
detection bounding boxes in `MapStage.tsx` (the `CanvasGeoJSON` alias, its
`detectionLayerKey` remount key, and the `onEachDetection` `onEachFeature`
binder in `GaiaMap.tsx`).
**Replacement:** the box layer is now `geomDisplayedDetectionsGeoJSON.features
.map(... <Polygon/>)` — one canvas-rendered `<Polygon>` component per
detection, the same per-feature React pattern already used for the icon
markers, dots, uncertainty halos and tracks.

## Why

- **The `<GeoJSON>` box layer silently rendered nothing.** Detection icon
  markers rendered fine from `filteredDetectionsGeoJSON`, but the box layer —
  fed the *same* features — never painted. All of its props (`data`, `key`,
  `renderer`, `style`, `onEachFeature`) were individually correct, yet the
  layer produced no visible polygons. `<GeoJSON>` builds every feature in one
  `L.GeoJSON.addData` loop inside its constructor; a single edge case fails the
  whole layer with no error surfaced.
- **The per-feature pattern is reactive and isolated.** A `features.map()` of
  `<Polygon>` re-renders naturally when data changes (no `key`-remount hack),
  and one bad geometry skips just that box instead of killing the layer.
- **It matches the rest of the file.** Icon markers, dots, uncertainty circles
  and tracks are all per-feature React components; the box layer was the lone
  `<GeoJSON>` outlier.

## How

- `geojsonToLatLngs(geometry)` in `_helpers.ts` converts a GeoJSON
  `Polygon`/`MultiPolygon` (coords `[lon,lat]`) to the nested Leaflet
  `[lat,lng]` array `<Polygon positions>` expects; returns `null` for
  `Point`/missing/degenerate geometry so the caller skips it.
- Each `<Polygon>` takes `pathOptions={getDetectionStyle(feature)}` and selects
  the detection on click.
- **No custom `renderer`.** The boxes use the map's default SVG renderer. The
  shared `detectionCanvasRenderer` (`L.canvas()`) never painted anything — it
  gated *both* the old `<GeoJSON>` box layer and the first `<Polygon>` revision,
  and neither rendered. The default per-map SVG renderer (the same one the
  uncertainty-halo `<Circle>`s use) is reliable. Detection counts in the box
  layer are bounded enough that SVG performance is not a concern.

## Trade-offs accepted

- The box no longer binds its own popup/tooltip (the old `onEachDetection`).
  The detection's icon marker already provides the popup and hover label, so
  this is not a user-visible loss; the box stays click-to-select.

## Cross-references

- [map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [why-bbox-toggle-removed.md](why-bbox-toggle-removed.md)
