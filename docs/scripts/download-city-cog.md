# Sentinel-2 COG Imagery Downloader

**Path:** [scripts/download_city_cog.py](../../scripts/download_city_cog.py)  
**Lines:** ~223  
**Depends on:** Python Standard Library (`urllib`, `json`, `argparse`), `rasterio` (optional metadata verification)

## Purpose
Geocodes any city, country, or region name and downloads the latest high-resolution Cloud-Optimized GeoTIFF (COG) true-color composite scene from the Sentinel-2 L2A archive on AWS.

## Why this design
GEOINT analysts often require fast access to ad-hoc satellite maps of specific global regions. Instead of requiring complex manual searches, this script utilizes public APIs:
1. **OSM Nominatim API** is used to dynamically geocode the query string to its geographic bounds without needing an API key.
2. **Element 84 Earth Search STAC API** is used to perform high-speed, spatial-temporal catalog queries.
3. **Centroid-Based Falling Back** resolves the potential issue of large geographic search boxes (e.g. requesting "France" or "Iran") by automatically narrowing down the search window to a standard 22km x 22km grid around the center centroid, preventing excessive network transfers and multi-tile overlaps.
4. **Iterative Search Backoff** progressively broadens the temporal search window (from 30 days to up to 360 days) to guarantee that the analyst receives a clear, low-cloud (<= 10% defaults) scene.

## Key symbols
- [`geocode_location(query)`](../../scripts/download_city_cog.py#L18-L47) — Geocodes a text query to lat/lon bounding box.
- [`search_stac(bbox_coords, max_cloud_cover, days_back)`](../../scripts/download_city_cog.py#L49-L107) — Queries Element 84 STAC API for matching Sentinel-2 scenes.
- [`download_cog(scene, output_path)`](../../scripts/download_city_cog.py#L109-L162) — Downloads the true-color visual COG asset in chunks.
- [`main()`](../../scripts/download_city_cog.py#L164-L219) — Main CLI entrypoint, orchestrating the pipeline and printing optional rasterio validation metrics.

## Inputs / Outputs
- **Input**:
  - `location`: Positional argument specifying the geographic place (e.g. "Paris", "Washington D.C.").
  - `--output` / `-o`: Custom destination path (defaults to `/nvme/osint/sample/<location>_sentinel2_cog.tif`).
  - `--cloud-cover` / `-c`: Float limit for cloud-cover filtering (defaults to `10.0%`).
  - `--days` / `-d`: Initial temporal search window in days (defaults to `30`).
- **Output**:
  - A metric projection, 3-band RGB georeferenced Cloud-Optimized GeoTIFF (COG) stored at the designated destination.

## Failure modes
- **Geocoding failures**: Triggers clean stderr exit if query is unresolvable or network times out.
- **STAC empty responses**: Progressively broadens time window; fails if no scene meets cloud-cover requirements.
- **S3 connection timeouts**: Standardizes 180s socket timeouts to handle heavy high-res visual assets.
- **Broken downloads**: Deletes incomplete local output files automatically on caught execution exceptions to avoid corrupted images.

## Cross-references
- Workspace samples: [tehran_sentinel2_cog.tif](../../sample/tehran_sentinel2_cog.tif)
- Used by: Manual GEOINT operations and ad-hoc analysts.
