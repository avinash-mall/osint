# Removed the `showBbox` / BBOX Toggle — Detection Boxes Are Always On

## Decision

**Removed:** the `showBbox` boolean state in `GaiaMap.tsx` and the **BBOX** button in `LayerPanel.tsx`.
**Replacement:** the detection bounding-box layer (`<GeoJSON>` canvas layer in `MapStage.tsx`) **always renders**. Box shape still chosen by the existing GEOM toolbar (`HBB` / `OBB` / `MASK`, `bboxMode` state); default is now `OBB`.

## Why

- **The toggle hid the analyst's primary geo-truth marker.** On the Geoint page detections rendered only as category icon markers; the box outline — the actual georeferenced extent of the object — was gated behind a button most analysts never found. For a defence-analysis tool the box is not an optional overlay.
- **The toggle was also logically broken.** `showDetectionCenterMarkers` was `count <= LIMIT || !showBbox`. The `|| !showBbox` term made the dense-scene *dot* fallback layer unreachable dead code and caused thousands of icon markers to render when `showBbox` was off and `count > LIMIT`. Dropping the term restored the documented behaviour ("above 800 the map renders dots instead of icon markers").
- **`MASK` was the wrong default** — draws the raw irregular detection outline → nothing on screen read as a "bounding box". `OBB` (oriented rectangle, falling back to the raw `geom` quad when oriented-box metadata is absent) is what analysts expect.

## Resulting behaviour

- Detections ≤ `DETECTION_CENTER_MARKER_LIMIT` (800) in view: **icon markers + boxes** render together.
- Detections > 800 in view: **dots + boxes** render together.
- The box layer always renders; `makeDetectionStyle` (`_helpers.ts`) draws a solid, category-coloured outline (weight 2) so small boxes stay perceptible.
- The GEOM toolbar still lets the analyst switch box shape on demand.

## Trade-offs accepted

- Analysts can no longer hide boxes entirely. Intentional — the GEOM toolbar covers the only legitimate need (changing box shape), and the layer-level `AI Detections` toggle in `LayerPanel` still hides the whole detection layer when needed.

## Cross-references

- [map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
