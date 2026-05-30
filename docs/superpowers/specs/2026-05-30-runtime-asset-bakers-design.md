# Runtime Asset Bakers → `./assets`

**Date:** 2026-05-30
**Status:** Approved design, pending implementation plan
**Topic:** Move all four geo-asset downloads (OSRM, DEM, basemap, terrain) out of image-build BuildKit cache mounts and into runtime baker containers that write directly to the host `./assets` folder.

## Problem

The current pipeline bakes geo-assets **at image-build time** into ephemeral BuildKit cache mounts (`/cache/osrm`, `/cache/dem`, `/cache/terrain`, `/cache/basemap`), then rsyncs them out to the host at container start. Two structural failures result:

1. **Killed/cancelled `RUN` steps roll back their cache-mount writes.** A 30 GB host cannot run a full-planet (or even all-Asia) `osrm-extract`; `systemd-oomd` kills it, and the build then **re-downloads** the 14.8 GB PBF on every retry. Verified: after a killed build, `/cache/osrm` is empty.
2. **Orphaned cache.** Cancelled DEM/terrain/basemap builds left ~590 GB of cache blobs that new builds **cannot reuse** (new builds see empty mounts), reported as ~1.1 TB reclaimable. Disk sits at 83 % full (301 GB free).

Build-time `RUN` steps physically cannot write to the host `./assets` folder (build sandbox is isolated), which is why the current design needs the bake-into-image + rsync-out dance.

## Goals

- All four downloads land **directly in `./assets`** on the host, persistent and resumable across crashes/interruptions.
- The heavy fetch/extract becomes an explicit, opt-in step (`docker compose --profile bake up`), not part of `docker compose up`.
- `osrm-extract` runs in a normal runtime container: can use host swap, no contention from parallel image builds.
- **Air-gap rule (CLAUDE.md #8) preserved:** the default `docker compose up` performs zero downloads; the deployed runtime only *reads* `./assets`.

## Non-goals

- Fonts, reference-corpora chips, calibration temperatures stay baked into the `assets` image (they are operator-built/derived, not network downloads). Out of scope.
- No change to which datasets/regions are fetched by default; region/size knobs are unchanged, only relocated from build args to baker env.

## Architecture

A `bake` Compose profile that **writes** `./assets`, and a runtime stack that only **reads** `./assets`.

```
CONNECTED HOST (one-time prep)          AIR-GAPPED RUNTIME
  docker compose --profile bake up        docker compose up -d
        │                                       │
        ▼  writes                               ▼  reads :ro
   ./assets/osrm   ◄──────────────────────►  osrm service
   ./assets/dem    ◄──────────────────────►  backend / worker
   ./assets/static/basemap  ◄────────────►  assets (nginx)
   ./assets/static/terrain  ◄────────────►  assets (nginx)
```

### Baker services (`profiles: [bake]`, `restart: "no"`)

Each bind-mounts its target dir and runs the existing, already-idempotent script directly into it. `basemap-baker` and `terrain-baker` share one slim python image.

| Service | Base image | Bind mount | Command (script) | Env knobs |
|---|---|---|---|---|
| `osrm-baker` | `ghcr.io/project-osrm/osrm-backend:v6.0.0` | `./assets/osrm:/data` | `build_offline_osrm.sh` (DATA_DIR=/data) | `PLANET_PBF_URL` |
| `dem-baker` | `ghcr.io/osgeo/gdal:ubuntu-small-3.9.2` | `./assets/dem:/data/dem` | `build_offline_dem.py --out /data/dem` | `DEM_CONCURRENCY`, `DEM_LAT/LON_MIN/MAX` |
| `basemap-baker` | `python:3.11-slim` | `./assets/static/basemap:/out` | `build_offline_basemap.py --out /out` | `BASEMAP_ZOOM`, `BASEMAP_CONCURRENCY` |
| `terrain-baker` | `python:3.11-slim` | `./assets/static/terrain:/out` | `build_offline_terrain.py --out /out` | `TERRAIN_ZOOM`, `TERRAIN_CONCURRENCY` |

Properties:
- **Resumable:** scripts skip existing files; `build_offline_osrm.sh` uses `curl --continue-at -`. Writes go straight to the host bind mount, so a crash/Ctrl-C/OOM never rolls back. Re-run to continue.
- **Swap-capable:** ordinary runtime containers, so `osrm-extract` can use the host's 64 GB swap (unlike build cgroups).
- **No parallel-build contention:** run on their own, not alongside 7 image builds.
- **OSRM PBF retention:** `planet.osm.pbf` is **kept** in `./assets/osrm` after extract (operator decision) so a future re-extract needs no re-download.

### Runtime service changes

- `osrm` — remove `depends_on: { osrm-assets: service_completed_successfully }`. Keeps `./assets/osrm:/data:ro`, `command: osrm-routed --algorithm mld /data/planet.osrm`. If `planet.osrm` absent → healthcheck fails → backend `osrm_available()` False → `/api/analytics/routes` serves 503 (existing graceful behavior).
- `backend` / `worker` — remove `depends_on: { dem-assets: service_completed_successfully }`. Keep `./assets/dem:/data/dem:ro`. Missing DEM → analytics viewshed/LOS serve 503 (existing).
- `assets` (nginx) — stop baking basemap/terrain into the image. Bind-mount `./assets/static/basemap` and `./assets/static/terrain` read-only into the nginx html root. **Relax healthcheck** from "must serve `/basemap/0/0/0.png` and `/terrain/0/0/0.png`" to **`/healthz` only**, so the stack starts before tiles are baked (map simply lacks those overlays until baked). `nginx` keeps `depends_on: { assets: service_healthy }`.

### Removed services

- `dem-assets` and `osrm-assets` (build-time bake + rsync init containers) are **removed**. Their entrypoints/Dockerfiles are deleted or repurposed into the baker images.
- The `assets/Dockerfile` loses its basemap/terrain fetch stages; it keeps fonts, reference-corpora, and calibration baking.

## Disk handling

1. **Reclaim first:** `docker buildx prune -f` drops ~1.1 TB of orphaned/unreusable cache → free space 301 GB → ~1.4 TB. No data loss (blobs are inaccessible to new builds, verified empty).
2. **No migration:** cached asset data is orphaned; bakers re-fetch fresh into `./assets`. One-time cost, now resumable.

## Operator workflow

```bash
# Connected host, one-time:
docker buildx prune -f
docker compose --profile bake up osrm-baker dem-baker basemap-baker terrain-baker
docker compose up -d --build       # fast: no downloads

# Air-gap target:
#  copy ./assets + images, then:
docker compose up -d               # reads ./assets, never fetches
```

Region/size selection via `.env` (already set: `PLANET_PBF_URL=https://download.geofabrik.de/asia-latest.osm.pbf`).

## Failure modes

| Condition | Behavior |
|---|---|
| Baker interrupted/OOM | Partial data persists in `./assets`; re-run resumes. |
| `osrm-extract` OOM on too-large region | Killed, but PBF + partial outputs persist; operator picks a smaller `PLANET_PBF_URL` and re-runs (no re-download of unchanged PBF). |
| Runtime start with empty `./assets/osrm` | `osrm` unhealthy → routes 503. Stack still up. |
| Runtime start with empty `./assets/dem` | viewshed/LOS 503. Stack still up. |
| Runtime start with no baked tiles | `assets` healthy via `/healthz`; basemap/terrain overlays absent. Stack still up. |

## Docs to update (CLAUDE.md workflow)

- `docs/deployment/osrm-planet-bake.md`, `docs/deployment/dem-glo30-bake.md` — rewrite to baker workflow.
- `docs/deployment/offline-airgap-deployment.md` — new prep step ordering.
- `docs/scripts/build-offline-basemap.md`, `build-offline-terrain.md` — runtime-into-assets.
- New `docs/decisions/why-runtime-bakers-into-assets.md` — record the architecture change and the killed-RUN rollback rationale.
- Module docs for each changed Dockerfile/compose/entrypoint; `docs/INDEX.txt`.
- Product tour check: no interactive frontend controls change → `grep -r data-tour` confirm unaffected (expected none).

## Verification

- `docker compose config` parses; `bake`-profile services hidden from default `up`.
- `docker compose --profile bake up dem-baker` on a tiny bbox (`DEM_LAT_MIN=20 DEM_LAT_MAX=22 DEM_LON_MIN=50 DEM_LON_MAX=52`) writes tiles + `glo30.vrt` into `./assets/dem`.
- `docker compose --profile bake up osrm-baker` with the Asia URL produces `./assets/osrm/planet.osrm`; interrupting mid-extract and re-running does not re-download the PBF.
- `docker compose up -d` with populated `./assets`: `osrm` healthy, `/route/v1` responds; with empty `./assets`: stack still comes up, affected endpoints 503.
- `assets` healthcheck passes with no baked tiles.
```
