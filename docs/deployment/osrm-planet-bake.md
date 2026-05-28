# Planet OSRM Bake

The planet OSRM MLD dataset that backs `/api/analytics/routes` is **baked automatically** during `docker compose up -d --build` on a connected host. Once the build is done, the resulting `sentinel-osrm-assets:offline` image holds ~150-200 GB of OSRM artifacts and is air-gappable via `docker save | gzip`. No separate profile invocation is required.

## How it works

- [osrm-assets/Dockerfile](../../osrm-assets/Dockerfile) — multi-stage build. The fetcher stage uses the upstream `ghcr.io/project-osrm/osrm-backend:v6.0.0` image and runs [`scripts/build_offline_osrm.sh`](../../scripts/build_offline_osrm.sh) against a BuildKit cache mount at `/cache/osrm`. The script curl-downloads `planet-latest.osm.pbf`, then runs `osrm-extract -p /opt/car.lua`, `osrm-partition`, and `osrm-customize` to produce the MLD dataset. The final stage is an alpine image holding the baked artifacts at `/opt/baked-osrm/`.
- [osrm-assets/scripts/entrypoint.sh](../../osrm-assets/scripts/entrypoint.sh) — on first container start, rsyncs `/opt/baked-osrm/` onto the `osrm_data` named volume (mounted at `/data`). Subsequent starts compare `MANIFEST.sha256` and no-op when matching. Exits 0 either way.
- [docker-compose.yml](../../docker-compose.yml) — the runtime `osrm` service waits via `depends_on: { osrm-assets: { condition: service_completed_successfully } }` before running `osrm-routed --algorithm mld /data/planet.osrm`.

## Prerequisites

- Docker + Docker Compose v2.20+
- ~250 GB free on the disk hosting `osrm_data`
- ~250 GB free Docker storage
- ~16 GB RAM during the `osrm-extract` step (planet extract is memory-heavy)
- ~6-24 h end-to-end on first build (~80 GB PBF + ~3-6 h CPU extract)

## Default bake (planet-latest)

```bash
docker compose up -d --build
```

The `osrm-assets` service builds (downloading planet PBF + running the OSRM pipeline), then runs its entrypoint, rsyncs to `osrm_data`, and exits 0. The runtime `osrm` service then starts.

## Regional bake (much faster smoke build)

Override the PBF URL to a Geofabrik regional extract:

```bash
PLANET_PBF_URL=https://download.geofabrik.de/asia/gcc-states-latest.osm.pbf \
docker compose up -d --build
```

Bake completes in 30-90 minutes for a single-country extract. The runtime `osrm` service is region-agnostic — it serves whatever PBF was baked.

## Slim build (no OSRM)

For CI / smoke tests where routing doesn't need to work:

```bash
OSRM_ENABLED=0 docker compose up -d --build
```

The image is built but ships a stub `MANIFEST.sha256=skipped`. The entrypoint exits 0 without rsyncing; the runtime `osrm` service then fails healthcheck (no `/data/planet.osrm`). Backend's `osrm_available()` returns False; `/api/analytics/routes` returns 503 honestly.

## Re-bake / refresh

The BuildKit cache mount preserves the planet PBF and extract outputs across rebuilds. To force a clean re-bake (e.g. to pull a fresh planet):

```bash
docker builder prune --filter type=exec.cachemount
docker compose build --no-cache osrm-assets
docker compose up -d osrm-assets osrm
```

## Verify

```bash
curl -s http://localhost:3000/api/analytics/capabilities | jq
# expect: {"dem": ..., "routing": true, "demo_fixtures": false}

curl -s -X POST http://localhost:3000/api/analytics/routes \
  -H "Content-Type: application/json" \
  -d '{"observer":{"latitude":51.5074,"longitude":-0.1278},"destination":{"latitude":48.8566,"longitude":2.3522}}' \
  | jq '.result.features | length'
# expect: 1-3 (London → Paris)
```

The map workspace's Analytics panel should show `ROUTING · OK` in the bottom chip.

## Air-gap shipping

```bash
# on connected host
docker save sentinel-osrm-assets:offline ghcr.io/project-osrm/osrm-backend:v6.0.0 \
  | gzip > sentinel-osrm.tar.gz

# on air-gap host
gunzip -c sentinel-osrm.tar.gz | docker load
docker compose up -d   # no --build
```

The volume is re-seeded from the image automatically; the runtime `osrm` service starts after the seed completes.

## Cross-references

- [backend/routing-osrm.md](../backend/routing-osrm.md)
- [decisions/why-osrm-replaced-networkx.md](../decisions/why-osrm-replaced-networkx.md)
- [decisions/why-dem-osrm-as-sibling-baker-images.md](../decisions/why-dem-osrm-as-sibling-baker-images.md)
- [volume-mounts-and-paths.md](volume-mounts-and-paths.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
