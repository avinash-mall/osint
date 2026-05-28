# Worldwide DEM Bake — Copernicus GLO-30

One-time bake of the global Copernicus GLO-30 (30 m) DEM mosaic that backs `/api/analytics/viewshed`, `/api/analytics/los`, and `/api/analytics/elevation`. Run once on a host with internet access; the resulting `dem_data` volume is then air-gappable.

## Prerequisites

- Docker + Docker Compose
- ~170 GB free on the disk hosting `dem_data` (allows for the VRT, tiles, and headroom)
- ~6-24 h depending on link speed (~150 GB to fetch)

## Bake

```bash
docker compose --profile bake-dem up --build dem-baker
```

This runs [`scripts/build_offline_dem.py`](../../scripts/build_offline_dem.py) inside a slim GDAL container. The script:

1. Plans every 1° × 1° cell on Earth (lat -90..90, lon -180..180).
2. Fetches each tile from `https://copernicus-dem-30m.s3.amazonaws.com/.../Copernicus_DSM_COG_10_<NS><lat>_00_<EW><lon>_00_DEM.tif` with exponential backoff on 429/5xx. Ocean-only cells return 404 and are skipped silently.
3. Writes progress to `/data/dem/.progress.json` every 200 cells, so a crashed bake can be resumed by re-running the same command.
4. Once tiles are complete, runs `gdalbuildvrt` to produce `/data/dem/glo30.vrt` over every fetched tile.

Tune concurrency via `DEM_BAKE_CONCURRENCY` in `.env` (default 8). S3 throttles aggressively above ~16 connections per IP.

## Smaller bake (regional)

The script accepts `--lat-min / --lat-max / --lon-min / --lon-max` for a regional bake. Edit the `command:` line in the `dem-baker` service, or run the script directly inside any GDAL-capable container:

```bash
docker run --rm \
  -v "$PWD/scripts/build_offline_dem.py:/opt/build.py:ro" \
  -v dem_data:/data/dem \
  ghcr.io/osgeo/gdal:ubuntu-small-3.9.2 \
  python3 /opt/build.py --out /data/dem --lat-min 20 --lat-max 35 --lon-min 50 --lon-max 65
```

## VRT-only re-mosaic

If you add tiles by hand and want to rebuild only the VRT:

```bash
docker compose --profile bake-dem run --rm dem-baker \
  python3 /opt/build_offline_dem.py --out /data/dem --vrt-only
```

## Verify

After the bake completes, bring the stack up and probe:

```bash
docker compose up -d
curl -s http://localhost:3000/api/analytics/capabilities | jq
# expect: {"dem": true, "routing": ..., "demo_fixtures": false}

curl -s 'http://localhost:3000/api/analytics/elevation?lat=27.9881&lon=86.9250'
# expect: {"elevation_m": ~8800}   # Everest summit
```

The map workspace's Analytics panel should show `DEM · OK` in the bottom chip.

## Attribution

Per the ESA Standard Licence, the bake drops an `ATTRIBUTION.txt` into `/data/dem/`. Do not strip it from operational deployments.

## Cross-references

- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [decisions/why-glo30-as-default-dem.md](../decisions/why-glo30-as-default-dem.md)
- [volume-mounts-and-paths.md](volume-mounts-and-paths.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
