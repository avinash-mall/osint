# City Imagery Downloader (Sentinel-2 + Esri World Imagery)

**Path:** [scripts/download_city_cog.py](../../scripts/download_city_cog.py)  
**Lines:** ~373  
**Depends on:** Python Standard Library (`urllib`, `json`, `argparse`, `math`, `io`); `rasterio` + `numpy` + `Pillow` (required only for the `esri` source and for optional Sentinel-2 metadata verification)

## Purpose
Geocodes any city, country, or region name and downloads a georeferenced GeoTIFF from one of two selectable sources (`--source`):
- **`sentinel2`** (default) — latest low-cloud Sentinel-2 L2A Cloud-Optimized GeoTIFF (COG) true-color composite at **10 m/px** from the AWS archive (radiometric, dated satellite data).
- **`esri`** — sub-meter **Esri World Imagery** basemap (~0.3–1.2 m/px depending on `--zoom`), fetched as XYZ tiles and stitched into a Web-Mercator (`EPSG:3857`) GeoTIFF.

## Why this design
GEOINT analysts often require fast access to ad-hoc satellite maps of specific global regions. Sentinel-2's 10 m/px is too coarse to resolve vehicles or small structures, so a sharper basemap source is offered alongside it. The script utilizes public APIs:
1. **OSM Nominatim API** dynamically geocodes the query string to geographic bounds without an API key.
2. **Element 84 Earth Search STAC API** performs high-speed spatial-temporal catalog queries for Sentinel-2.
3. **Centroid-Based Fallback** resolves large geographic search boxes (e.g. "France" or "Iran") by narrowing the Sentinel-2 search window to a ~22 km grid around the centroid.
4. **Iterative Search Backoff** progressively broadens the Sentinel-2 temporal window (30 → 360 days) to guarantee a clear, low-cloud (<= 10% default) scene.
5. **Esri tile mosaicking** carves a fixed square AOI (`--radius-km`) around the centroid — the full geocoded bbox would be millions of sub-meter tiles — converts it to the slippy-map XYZ tile range at `--zoom`, downloads each 256×256 tile, stitches them, and writes a georeferenced `EPSG:3857` COG. A `MAX_TILES` cap (6000) guards against runaway zoom/AOI combinations. Web-Mercator is used directly because Esri tiles are natively square in `EPSG:3857`.

## Key symbols
- [`geocode_location(query)`](../../scripts/download_city_cog.py#L28-L58) — Geocodes a text query to a lat/lon bounding box.
- [`search_stac(bbox_coords, max_cloud_cover, days_back)`](../../scripts/download_city_cog.py#L60-L121) — Queries Element 84 STAC API for matching Sentinel-2 scenes.
- [`download_cog(scene, output_path)`](../../scripts/download_city_cog.py#L123-L189) — Downloads the Sentinel-2 true-color visual COG asset in chunks.
- [`_deg2tile` / `_tile_3857_bounds`](../../scripts/download_city_cog.py#L197-L209) — Slippy-map tile math: lat/lon → XYZ tile, and tile → `EPSG:3857` bounds.
- [`download_esri(bbox_coords, output_path, zoom, radius_km)`](../../scripts/download_city_cog.py#L211-L290) — Fetches, stitches, and georeferences Esri World Imagery tiles into a COG.
- [`main()`](../../scripts/download_city_cog.py#L292-L373) — CLI entrypoint; dispatches on `--source` and prints optional rasterio validation metrics.

## Inputs / Outputs
- **Input**:
  - `location`: Positional argument specifying the geographic place (e.g. "Paris", "Washington D.C.").
  - `--source` / `-s`: `sentinel2` (default) or `esri`.
  - `--output` / `-o`: Custom destination path (defaults to `/nvme/osint/sample/<location>_<sentinel2_cog|esri>.tif`).
  - `--cloud-cover` / `-c`: *(sentinel2)* Float cloud-cover limit (default `10.0%`).
  - `--days` / `-d`: *(sentinel2)* Initial temporal search window in days (default `30`).
  - `--zoom` / `-z`: *(esri)* XYZ zoom level — z17 ~1.2 m/px, z18 ~0.6 m/px, z19 ~0.3 m/px (default `18`).
  - `--radius-km`: *(esri)* Half-width of the square AOI in km around the centroid (default `2.0` ⇒ 4 km box).
- **Output**:
  - `sentinel2`: a 3-band RGB georeferenced UTM COG at 10 m/px.
  - `esri`: a 3-band RGB georeferenced `EPSG:3857` COG at sub-meter GSD with overviews.

## Failure modes
- **Geocoding failures**: Clean stderr exit if the query is unresolvable or the network times out.
- **STAC empty responses** *(sentinel2)*: Progressively broadens time window; fails if no scene meets cloud-cover requirements.
- **S3 connection timeouts** *(sentinel2)*: 180s socket timeouts for heavy visual assets.
- **Tile-count overflow** *(esri)*: Aborts when the AOI/zoom would exceed `MAX_TILES` (6000); prompts lowering `--zoom` or `--radius-km`.
- **Tile download failure** *(esri)*: Any failed tile fetch aborts the run with the offending z/x/y.
- **Broken downloads**: Incomplete Sentinel-2 output files are deleted automatically on caught exceptions.

## Cross-references
- Workspace samples: [tehran_sentinel2_cog.tif](../../sample/tehran_sentinel2_cog.tif)
- Esri World Imagery is a basemap product with mixed/unknown acquisition dates and basemap licensing terms — suitable for internal analysis, not redistribution. See [docs/decisions/imagery-source-selection.md](../decisions/imagery-source-selection.md).
- Used by: Manual GEOINT operations and ad-hoc analysts.
