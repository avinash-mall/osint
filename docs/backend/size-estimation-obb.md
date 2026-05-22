# `backend/size_estimation.py` — Real-World OBB Dimensions

**Path:** [backend/size_estimation.py](../../backend/size_estimation.py)
**Lines:** ~115
**Depends on:** `pyproj`, `shapely`, `numpy`

## Purpose

Convert an oriented bounding box (OBB) + georeferencing → real-world dimensions: length (m), width (m), area (m²), bearing (° from true north). Exposes "this vessel is ~120 m long" to the operator.

## Why this design

- **Local UTM projection per detection** — lon/lat → local UTM zone for the detection's centroid → minimum-rotated-rectangle in meters. UTM-local minimizes scale distortion (matters for large objects).
- **Minimum rotated rectangle, not the OBB's literal edges** — SAM3's OBB is the mask's `cv2.minAreaRect`; this module re-fits a true minimum-area rectangle in geographic space after warping.
- **Bearing from north** — reported 0..180° (orientation, not heading; which "end" is the front is unknown).

## Key symbols

- [`local_utm_crs`](../../backend/size_estimation.py#L19) — `pyproj.CRS` for a given (lon, lat).
- [`_polygon_from_flat`](../../backend/size_estimation.py#L25) — worker's flat-coordinate OBB → Shapely polygon.
- [`_bearing_from_north_deg`](../../backend/size_estimation.py#L37).
- [`estimate_size`](../../backend/size_estimation.py#L45) — `(obb_coords, lat, lon) -> {length, width, area, bearing, length_uncertainty, ...}`.

## Failure modes

- OBB has <4 distinct vertices → `{}`; UI hides the size widget.
- Centroid lat/lon missing → `{}`.

## Cross-references

- Tests: [backend/tests/test_size_estimation.py](../../backend/tests/test_size_estimation.py)
- Output consumed by [`SelectionPanel.tsx`](../../frontend/src/components/map/SelectionPanel.tsx) details tab.
