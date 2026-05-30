# `bakers/dem/Dockerfile` — DEM Runtime Baker

**Path:** [bakers/dem/Dockerfile](../../bakers/dem/Dockerfile)
**Lines:** ~13
**Depends on:** `ghcr.io/osgeo/gdal:ubuntu-small-3.9.2`; `scripts/build_offline_dem.py`; env `DEM_CONCURRENCY`, `DEM_LAT_MIN`, `DEM_LAT_MAX`, `DEM_LON_MIN`, `DEM_LON_MAX`

## Purpose

Runtime baker image for the Copernicus GLO-30 worldwide DEM. Runs under the `bake` Compose profile, downloads GLO-30 `.tif` tiles from the Copernicus S3 bucket, builds `glo30.vrt`, and writes output directly into the host-bind-mounted `./assets/dem`. Replaces the former `dem-assets` build-time init container.

## Why this design

BuildKit `RUN` cache mounts roll back on cancellation — a killed DEM build lost all fetched tiles and required a full re-download. A runtime container writes directly to the host bind mount; partial downloads are preserved across interruptions. Region bounds are passed via Compose `command:` so the same image serves any region without rebuild. See [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md).

## Key symbols

- [`build_offline_dem.py`](../../scripts/build_offline_dem.py#L1) — copied to `/app/build_offline_dem.py`; Compose supplies the full `python /app/build_offline_dem.py --out /data/dem ...` invocation.
- No `CMD` in the Dockerfile — Compose provides the complete command with env-driven bounds.

## Inputs / Outputs

- **Input:** `DEM_LAT/LON_MIN/MAX` and `DEM_CONCURRENCY` env vars; `/data/dem` bind-mounted from `./assets/dem`.
- **Output:** `./assets/dem/glo30/*.tif` + `./assets/dem/glo30.vrt` + `./assets/dem/MANIFEST.sha256` + `./assets/dem/ATTRIBUTION.txt`.

## Failure modes

- Download interrupted: tile files already on disk are skipped on re-run (idempotent); re-run resumes.
- Missing DEM at runtime: `dem_available()` returns False; viewshed/LOS endpoints return 503; stack still starts.
- Copernicus S3 throttling: reduce `DEM_CONCURRENCY`; retry.

## Cross-references

- [deployment/dem-glo30-bake.md](dem-glo30-bake.md) — operator runbook
- [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md)
- [decisions/why-glo30-as-default-dem.md](../decisions/why-glo30-as-default-dem.md)
- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
