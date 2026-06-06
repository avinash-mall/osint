# Imagery Source Selection in download_city_cog.py

## Decision

**Added:** an Esri World Imagery source to [scripts/download_city_cog.py](../../scripts/download_city_cog.py), selectable via `--source {sentinel2,esri}`. Sentinel-2 remains the default; the existing STAC flow is unchanged.

## Why

- **10 m/px is too coarse for object-scale GEOINT.** Sentinel-2's `visual` asset is genuinely 10 m/px (verified: produced GeoTIFFs are 10980×10980 at `res=10.0`, EPSG:32639) — that is the satellite's native RGB ceiling, not a script bug. A car (~4 m) is sub-pixel; small structures are a few fuzzy pixels. Analysts comparing against the sub-meter aerial sample chips (`austin*`, `vienna*`, `chicago*`) correctly perceive Sentinel-2 as blurry.
- **Esri World Imagery is the only free source giving sub-meter for an arbitrary named city, worldwide.** ~0.3–1.2 m/px depending on zoom. A z18 mosaic is ~17× sharper than Sentinel-2 (0.6 m vs 10 m nominal).
- **Keep both, don't replace.** Sentinel-2 is dated, radiometric satellite data (known acquisition time, multispectral lineage); Esri is a sharper but undated basemap mosaic. They serve different needs, so the source is a runtime flag.

## Why these mechanics

- **Fixed AOI instead of full bbox.** At sub-meter GSD the whole geocoded bbox (a city/country) would be millions of tiles. `download_esri` carves a `--radius-km` square around the centroid and enforces a `MAX_TILES=6000` cap (~1.2 GB mosaic ceiling).
- **Native EPSG:3857.** Esri XYZ tiles are square in Web Mercator, so the mosaic is georeferenced directly in `EPSG:3857` via `from_bounds`, avoiding a reprojection step. Sentinel-2 output stays in its native UTM.

## Trade-offs accepted

- **Basemap licensing.** Esri World Imagery carries basemap terms — fine for internal analysis, not for redistribution. Stated in `--source esri` output and the module doc.
- **Mixed/unknown acquisition dates.** Esri tiles have no per-pixel capture timestamp, unlike Sentinel-2's STAC `datetime`. Analysts needing dated imagery must use `sentinel2`.
- **Online at run time.** Tile fetch hits `server.arcgisonline.com`. This is a data-prep script run with connectivity, not part of the air-gapped runtime stack, so it does not violate the offline-runtime rule.

## Alternatives considered

- **Maxar Open Data STAC** (~0.5 m, clean satellite COGs, legally simplest) — rejected as the primary path because it only covers disaster-event AOIs, so most arbitrary cities have no coverage.
- **NAIP** (0.6–1 m) — US-only; not viable for a global tool.
- **Sentinel-2 super-resolution (ML)** — keeps 10 m provenance but fabricates detail; out of scope for a downloader.
