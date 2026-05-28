# `backend/terrain.py` — DEM Viewshed + Line-of-Sight

**Path:** [backend/terrain.py](../../backend/terrain.py)
**Lines:** ~248
**Depends on:** `rasterio`, `numpy`, DEM at `${DEM_PATH:-/data/dem/glo30.vrt}`

## Purpose

Two operator analytics: line-of-sight between two points, and viewshed (all points visible from an observer) on the DEM. Surfaces as `POST /api/analytics/los` and `POST /api/analytics/viewshed`.

## Why this design

- **CPU-only ray-cast** — DEM loaded once, indexed by lat/lon. LOS query traces a ray sampled along the great-circle path; viewshed sweeps rays in azimuth.
- **`k=0.13` atmospheric refraction** — standard terrestrial-visibility value; adds an Earth-curvature correction term to elevation deltas.
- **Tiled GLO-30 mosaic, single VRT.** Default `DEM_PATH` points at `/data/dem/glo30.vrt`, a `gdalbuildvrt` mosaic over ~26,000 1°-tile Copernicus GLO-30 GeoTIFFs (~150 GB worldwide) populated by [`scripts/build_offline_dem.py`](../../scripts/build_offline_dem.py). `rasterio.open()` reads VRTs through the same path as a single GeoTIFF, so the module is agnostic to whether the DEM is one file or a tiled mosaic. See [decisions/why-glo30-as-default-dem.md](../decisions/why-glo30-as-default-dem.md).
- **Fixture fallback** — `dem_available()` lets the analytics router return `mode: "fixture_no_dem"` instead of erroring when the VRT is missing.

## Key symbols

- [`dem_path`](../../backend/terrain.py#L37), [`dem_available`](../../backend/terrain.py#L41).
- [`_open_dem`](../../backend/terrain.py#L46), [`reset_dem_cache`](../../backend/terrain.py#L52).
- [`haversine_m`](../../backend/terrain.py#L56), [`_meters_per_degree`](../../backend/terrain.py#L64), [`_curvature_drop_m`](../../backend/terrain.py#L71).
- [`sample_elevation`](../../backend/terrain.py#L78) — `(lat, lon) -> elevation_m | None`.
- [`line_of_sight`](../../backend/terrain.py#L99) — segment-by-segment ray with refraction.
- [`viewshed`](../../backend/terrain.py#L162) — azimuth sweep.

## Failure modes

- DEM VRT or tiles missing → `dem_available()` is False; router emits 503 unless `ANALYTICS_ALLOW_FIXTURES=1`.
- Query outside DEM extent (e.g. an ocean-only 1° cell not present in the GLO-30 mirror) → `sample_elevation` returns `None`; LOS treats the missing sample as opaque.

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [scripts/build-offline-terrain.md](../scripts/build-offline-terrain.md)
- [deployment/dem-glo30-bake.md](../deployment/dem-glo30-bake.md)
- [deployment/volume-mounts-and-paths.md](../deployment/volume-mounts-and-paths.md)
- [decisions/why-glo30-as-default-dem.md](../decisions/why-glo30-as-default-dem.md)
