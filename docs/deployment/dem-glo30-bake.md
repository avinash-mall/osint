# Worldwide DEM Bake — Copernicus GLO-30

The worldwide Copernicus GLO-30 (30 m) DEM is **baked automatically** during `docker compose up -d --build` on a connected host. Once the build is done, the resulting `sentinel-dem-assets:offline` image holds ~150 GB of tile mosaic and is air-gappable via `docker save | gzip`. No separate profile invocation is required.

## How it works

- [dem-assets/Dockerfile](../../dem-assets/Dockerfile) — multi-stage build. The fetcher stage uses `ghcr.io/osgeo/gdal:ubuntu-small-3.9.2` and runs [`scripts/build_offline_dem.py`](../../scripts/build_offline_dem.py) against a BuildKit cache mount at `/cache/dem`. The final stage is an alpine image holding the baked tiles at `/opt/baked-dem/`.
- [dem-assets/scripts/entrypoint.sh](../../dem-assets/scripts/entrypoint.sh) — on first container start, rsyncs `/opt/baked-dem/` onto the `dem_data` named volume (mounted at `/data/dem`). Subsequent starts compare `MANIFEST.sha256` and no-op when the volume matches the image. Exits 0 either way.
- [docker-compose.yml](../../docker-compose.yml) `backend` + `worker` services wait via `depends_on: { dem-assets: { condition: service_completed_successfully } }`.

## Prerequisites

- Docker + Docker Compose v2.20+ (for `service_completed_successfully`)
- ~170 GB free on the disk hosting `dem_data` (tiles + VRT + headroom)
- ~170 GB free Docker storage (the image itself holds the baked data)
- ~6-24 h of fetch time on first build depending on link speed

## Default bake (worldwide)

```bash
docker compose up -d --build
```

That's it. The `dem-assets` service builds (downloading ~150 GB), then runs its entrypoint, rsyncs to `dem_data`, and exits 0. The backend then starts.

## Regional bake (much faster smoke build)

Override the fetch bbox via build args:

```bash
DEM_LAT_MIN=20 DEM_LAT_MAX=35 \
DEM_LON_MIN=50 DEM_LON_MAX=65 \
docker compose up -d --build
```

The bbox is exclusive on max — the example covers Gulf states. Bake completes in 10-30 minutes.

## Slim build (no DEM)

For CI / smoke tests where viewshed/LOS don't need to work:

```bash
DEM_ENABLED=0 docker compose up -d --build
```

The image is built but ships a stub `MANIFEST.sha256=skipped`. The entrypoint exits 0 without rsyncing. Backend's `dem_available()` returns False; `/api/analytics/viewshed` and `/api/analytics/los` return 503 honestly.

## Re-bake / refresh

The BuildKit cache mount preserves the fetched tiles across rebuilds, so re-running `docker compose build dem-assets` only fetches missing tiles. To force a clean re-bake from scratch:

```bash
docker builder prune --filter type=exec.cachemount
docker compose build --no-cache dem-assets
docker compose up -d dem-assets backend worker
```

## Verify

```bash
curl -s http://localhost:3000/api/analytics/capabilities | jq
# expect: {"dem": true, "routing": ..., "demo_fixtures": false}

curl -s 'http://localhost:3000/api/analytics/elevation?lat=27.9881&lon=86.9250'
# expect: {"elevation_m": ~8800}   # Everest summit
```

The map workspace's Analytics panel should show `DEM · OK` in the bottom chip.

## Air-gap shipping

```bash
# on connected host
docker save sentinel-dem-assets:offline | gzip > sentinel-dem-assets.tar.gz

# on air-gap host
gunzip -c sentinel-dem-assets.tar.gz | docker load
docker compose up -d   # no --build
```

The volume is re-seeded from the image automatically.

## Attribution

Per the ESA Standard Licence, the bake drops an `ATTRIBUTION.txt` into `/data/dem/`. Do not strip it from operational deployments.

## Cross-references

- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [decisions/why-glo30-as-default-dem.md](../decisions/why-glo30-as-default-dem.md)
- [decisions/why-dem-osrm-as-sibling-baker-images.md](../decisions/why-dem-osrm-as-sibling-baker-images.md)
- [volume-mounts-and-paths.md](volume-mounts-and-paths.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
