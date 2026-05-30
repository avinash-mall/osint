# Worldwide DEM Bake — Copernicus GLO-30

The worldwide Copernicus GLO-30 (30 m) DEM is fetched and built by the **`dem-baker`** runtime container (Compose `bake` profile). The baked tiles + VRT land directly on the host filesystem at `./assets/dem/`, where they survive `docker system prune` and ship as a plain folder — copy `./assets/dem/` to another machine, no `docker save` required.

## How it works

- [bakers/dem/Dockerfile](../../bakers/dem/Dockerfile) — FROM `ghcr.io/osgeo/gdal:ubuntu-small-3.9.2`, copies [`scripts/build_offline_dem.py`](../../scripts/build_offline_dem.py). The compose `command:` passes the full python invocation with env-driven bounds.
- The baker service (Compose profile `bake`) bind-mounts `./assets/dem` to `/data/dem` inside the container and runs `build_offline_dem.py --out /data/dem`. The script downloads GLO-30 `.tif` tiles from Copernicus S3, builds the `glo30.vrt` mosaic, and writes `MANIFEST.sha256`. Output goes directly to `./assets/dem/` on the host.
- [docker-compose.yml](../../docker-compose.yml) — `backend` and `worker` services bind-mount `./assets/dem:/data/dem:ro`. They no longer have a `dem-assets` init-container dependency; if the DEM folder is absent, `dem_available()` returns False and `/api/analytics/viewshed` / `/api/analytics/los` return 503 honestly.

## Prerequisites

- Docker + Docker Compose v2.20+
- ~170 GB free on the host disk for `./assets/dem/` (worldwide tiles + VRT + headroom)
- Network access from the docker host during the baker run

## Default bake (worldwide)

```bash
# 1. Reclaim orphaned BuildKit cache (one-time, recovers ~1.1 TB on a busy host)
docker buildx prune -f

# 2. Run the baker (worldwide by default)
docker compose --profile bake up dem-baker

# 3. Start the runtime stack (fast — no downloads)
docker compose up -d --build
```

The baker writes GLO-30 tiles, `glo30.vrt`, and `MANIFEST.sha256` directly into `./assets/dem/` and exits 0 when done. The backend and worker then read that folder.

## Region / size selection

Override the fetch bbox via env vars (in `.env` or inline). The `DEM_LAT/LON_*` vars are passed to `build_offline_dem.py --lat-min/--lat-max/--lon-min/--lon-max`:

```bash
# Gulf states only (10-30 minutes)
DEM_LAT_MIN=20 DEM_LAT_MAX=35 \
DEM_LON_MIN=50 DEM_LON_MAX=65 \
docker compose --profile bake up dem-baker

# Smoke test (tiny bbox, ~2 minutes)
DEM_LAT_MIN=27 DEM_LAT_MAX=29 \
DEM_LON_MIN=85 DEM_LON_MAX=87 \
docker compose --profile bake up dem-baker
```

The bbox is exclusive on max. Worldwide bake (~150 GB) takes 6–24 h depending on link speed.

## Resumability

If the baker is interrupted (Ctrl-C, OOM, network drop), partial tiles persist in `./assets/dem/glo30/`. Re-run the same command to resume — the script skips tiles that are already present on disk. Unlike the old BuildKit cache-mount approach, a cancelled run does not roll back any downloaded tiles.

## Root-owned output

The baker runs as root inside the container, so files written to `./assets/dem/` are owned by root on the host. This is consistent with the old init-container behaviour and ships correctly in the air-gap tarball/folder.

## Concurrency

Default concurrency is 8 parallel HTTPS workers. Copernicus S3 throttles per-IP at high concurrency; the default is a safe value. To adjust:

```bash
DEM_CONCURRENCY=4 docker compose --profile bake up dem-baker
```

## No-bake (empty DEM, honest 503)

For CI or deployments where viewshed/LOS are not needed, omit the baker run. The backend's `dem_available()` returns False; `/api/analytics/viewshed` and `/api/analytics/los` return 503 honestly. The rest of the stack is unaffected.

## Re-bake / refresh

GLO-30 is essentially static (annual updates at most). To force a clean re-bake of a region:

```bash
rm -rf ./assets/dem/glo30 ./assets/dem/glo30.vrt ./assets/dem/MANIFEST.sha256
docker compose --profile bake up dem-baker
```

Omit the `rm` to resume a partial download.

## Verify

```bash
curl -s http://localhost:3000/api/analytics/capabilities | jq
# expect: {"dem": true, "routing": ..., "demo_fixtures": false}

curl -s 'http://localhost:3000/api/analytics/elevation?lat=27.9881&lon=86.9250'
# expect: {"elevation_m": ~8800}   # Everest summit
```

The map workspace's Analytics panel should show `DEM · OK` in the bottom chip.

## Air-gap shipping

The data lives at `./assets/dem/` on the host.

```bash
# on connected host — archive the data
tar -C ./assets -cf - dem | zstd -T0 -o sentinel-dem.tar.zst

# on air-gap host (with the source repo already in place)
zstd -dc sentinel-dem.tar.zst | tar -C ./assets -xf -
docker compose up -d   # reads ./assets/dem, no --build, no rsync
```

The `dem-baker` image is **not required** on the air-gap host.

## Attribution

Per the ESA Standard Licence, the bake drops an `ATTRIBUTION.txt` into `./assets/dem/`. Do not strip it from operational deployments.

## Cross-references

- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [decisions/why-glo30-as-default-dem.md](../decisions/why-glo30-as-default-dem.md)
- [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md)
- [decisions/why-dem-osrm-as-sibling-baker-images.md](../decisions/why-dem-osrm-as-sibling-baker-images.md) (superseded by runtime bakers)
- [volume-mounts-and-paths.md](volume-mounts-and-paths.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
