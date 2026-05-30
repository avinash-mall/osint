# Planet OSRM Bake

The planet OSRM MLD dataset that backs `/api/analytics/routes` is fetched and built by the **`osrm-baker`** runtime container (Compose `bake` profile). The baked `planet.osrm*` artifacts land directly on the host filesystem at `./assets/osrm/`, where they survive `docker system prune` and ship as a plain folder — copy `./assets/osrm/` to another machine, no `docker save` required.

## How it works

- [bakers/osrm/Dockerfile](../../bakers/osrm/Dockerfile) — FROM `ghcr.io/project-osrm/osrm-backend:v6.0.0`, adds bash + curl, copies [`scripts/build_offline_osrm.sh`](../../scripts/build_offline_osrm.sh). `DATA_DIR=/data` and `PROFILE=/opt/car.lua` are set as image env defaults.
- The baker service (Compose profile `bake`) bind-mounts `./assets/osrm` to `/data` inside the container and runs `build_offline_osrm.sh`. The script curl-downloads the OSM PBF (`PLANET_PBF_URL`), then runs `osrm-extract -p /opt/car.lua`, `osrm-partition`, and `osrm-customize` to produce the MLD dataset. Output goes directly to `./assets/osrm/` on the host.
- [docker-compose.yml](../../docker-compose.yml) — the runtime `osrm` service bind-mounts `./assets/osrm:/data:ro` and runs `osrm-routed --algorithm mld /data/planet.osrm`. It no longer has an `osrm-assets` init-container dependency; if `planet.osrm` is absent the service fails its healthcheck and the backend's `osrm_available()` returns False, serving `/api/analytics/routes` as 503.

## Prerequisites

- Docker + Docker Compose v2.20+
- ~250 GB free on the host disk for `./assets/osrm/`
- Adequate RAM + swap for `osrm-extract` (see the 30 GB host constraint below)
- Network access from the docker host during the baker run

## 30 GB host constraint — do not use full-planet or all-Asia

On a 30 GB RAM host, a full-planet `osrm-extract` (~80 GB PBF, ~28 GB peak RAM) is killed by `systemd-oomd` before it finishes. Use a Geofabrik regional or country extract that fits your RAM budget. A single-country extract (e.g. Germany, ~3 GB PBF) peaks at ~2-4 GB RAM and completes in 15-60 minutes.

Set `PLANET_PBF_URL` in `.env` to the desired Geofabrik URL before running the baker:

```bash
# Country extract (recommended for 30 GB hosts)
PLANET_PBF_URL=https://download.geofabrik.de/europe/germany-latest.osm.pbf

# Gulf states (moderate size)
PLANET_PBF_URL=https://download.geofabrik.de/asia/gcc-states-latest.osm.pbf
```

Geofabrik extract index: <https://download.geofabrik.de/>

## Default bake

```bash
# 1. Reclaim orphaned BuildKit cache (one-time, recovers ~1.1 TB on a busy host)
docker buildx prune -f

# 2. Run the baker (reads PLANET_PBF_URL from .env)
docker compose --profile bake up osrm-baker

# 3. Start the runtime stack (fast — no downloads)
docker compose up -d --build
```

The baker writes `planet.osrm*` and `MANIFEST.sha256` directly into `./assets/osrm/` and exits 0 when done. The runtime `osrm` service then reads that folder.

## Region / size selection

Override `PLANET_PBF_URL` in `.env` or inline:

```bash
PLANET_PBF_URL=https://download.geofabrik.de/europe/great-britain-latest.osm.pbf \
docker compose --profile bake up osrm-baker
```

The runtime `osrm` service is region-agnostic — it serves whatever PBF was baked. The PBF file is **kept** in `./assets/osrm/` after extract so a future re-extract (e.g. profile change) requires no re-download.

## Resumability

If the baker is interrupted (Ctrl-C, OOM, power loss), partial output persists in `./assets/osrm/`. Re-run the same command to resume:

- The PBF download resumes via `curl --continue-at -` if partial.
- `osrm-extract` re-runs if interrupted (the `.osrm` output was not yet written).

No data is lost from the host bind mount — unlike the old BuildKit cache-mount approach, which rolled back all writes on cancellation.

## Root-owned output

The baker runs as root inside the container, so files written to `./assets/osrm/` are owned by root on the host. This is consistent with the old init-container behaviour and ships correctly in the air-gap tarball/folder.

## No-bake (empty osrm, honest 503)

For CI or deployments where routing is not needed, simply omit the baker run. The runtime `osrm` service will fail its healthcheck (no `/data/planet.osrm`). The backend's `osrm_available()` returns False; `/api/analytics/routes` returns 503 honestly. The rest of the stack is unaffected.

## Re-bake / refresh

To pull a fresh PBF and rebuild:

```bash
# Remove existing artifacts to force re-extract
rm -rf ./assets/osrm/planet.osrm* ./assets/osrm/MANIFEST.sha256

# Re-run (will re-download PBF if also removed, or resume from existing PBF)
docker compose --profile bake up osrm-baker
```

## Verify

```bash
curl -s http://localhost:3000/api/analytics/capabilities | jq
# expect: {"dem": ..., "routing": true, "demo_fixtures": false}

curl -s -X POST http://localhost:3000/api/analytics/routes \
  -H "Content-Type: application/json" \
  -d '{"observer":{"latitude":51.5074,"longitude":-0.1278},"destination":{"latitude":48.8566,"longitude":2.3522}}' \
  | jq '.result.features | length'
# expect: 1-3 (London → Paris, only works if Britain/Europe extract was baked)
```

The map workspace's Analytics panel should show `ROUTING · OK` in the bottom chip.

## Air-gap shipping

The data lives at `./assets/osrm/` on the host.

```bash
# on connected host — archive the data
tar -C ./assets -cf - osrm | zstd -T0 -o sentinel-osrm.tar.zst

# save the OSRM runtime image (contains osrm-routed, no data)
docker save ghcr.io/project-osrm/osrm-backend:v6.0.0 | gzip > osrm-backend.tar.gz

# on air-gap host
zstd -dc sentinel-osrm.tar.zst | tar -C ./assets -xf -
gunzip -c osrm-backend.tar.gz | docker load
docker compose up -d   # reads ./assets/osrm, no --build, no rsync
```

The `osrm-baker` image is **not required** on the air-gap host.

## Cross-references

- [backend/routing-osrm.md](../backend/routing-osrm.md)
- [decisions/why-osrm-replaced-networkx.md](../decisions/why-osrm-replaced-networkx.md)
- [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md)
- [decisions/why-dem-osrm-as-sibling-baker-images.md](../decisions/why-dem-osrm-as-sibling-baker-images.md) (superseded by runtime bakers)
- [volume-mounts-and-paths.md](volume-mounts-and-paths.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
