# Why geo-asset downloads moved to runtime baker containers

**Decision date:** 2026-05-30
**Status:** active — supersedes the build-time osrm-assets / dem-assets init-container pattern; extends [why-dem-osrm-as-sibling-baker-images.md](why-dem-osrm-as-sibling-baker-images.md).

## Context

The previous design ([why-dem-osrm-as-sibling-baker-images.md](why-dem-osrm-as-sibling-baker-images.md)) baked the four large geo-assets — OSRM planet extract, Copernicus GLO-30 DEM, Carto Dark basemap tiles, and OpenTopoMap terrain tiles — **at image-build time** using BuildKit cache mounts (`/cache/osrm`, `/cache/dem`, `/cache/terrain`, `/cache/basemap`). The baked data was then rsynced from image storage onto the host `./assets` folder by an init container (`dem-assets`, `osrm-assets`) at first container start.

Two structural failures emerged in production:

1. **Cancelled `RUN` steps roll back their BuildKit cache-mount writes.** A `docker buildx` step that is killed (by the OS, by the user, or by `systemd-oomd` OOM-killing the build cgroup) **rolls back** everything written to its `--mount=type=cache` volume. After a killed `osrm-extract`, the `/cache/osrm` mount was found empty on the next build attempt — causing a full re-download of the 14.8 GB PBF. This is a fundamental property of BuildKit cache mounts, not a configuration issue.

2. **Orphaned cache accumulation.** Cancelled DEM, terrain, and basemap builds left ~590 GB of cache blobs that subsequent builds **cannot reuse** (each new build sees a fresh empty mount). These blobs accumulated as reclaimable waste, filling the 30 GB host disk (1.1 TB reported reclaimable, 83 % disk full on the 1.4 TB array before reclaim).

A secondary constraint: **`RUN` steps inside a BuildKit sandbox cannot write to the host `./assets` directory.** The build sandbox is isolated from the host filesystem. This is why the old design needed the bake-into-image + rsync-out dance — there was no other path from build-time computation to the host folder.

Additionally, on a 30 GB RAM host, a full-planet `osrm-extract` (~80 GB PBF, peak ~28 GB RAM) could not complete inside a build cgroup because `systemd-oomd` OOM-killed it before it finished.

## Decision

Move all four geo-asset downloads out of BuildKit cache mounts and into **runtime baker containers** running under a dedicated `bake` Compose profile. Each baker bind-mounts its target subdirectory under `./assets` and writes directly to the host filesystem. The runtime stack (default `docker compose up`) only **reads** `./assets` — no network access, no rsync dance.

The four baker services:

| Service | Base image | Bind mount | Script |
|---|---|---|---|
| `osrm-baker` | `ghcr.io/project-osrm/osrm-backend:v6.0.0` | `./assets/osrm:/data` | `build_offline_osrm.sh` |
| `dem-baker` | `ghcr.io/osgeo/gdal:ubuntu-small-3.9.2` | `./assets/dem:/data/dem` | `build_offline_dem.py` |
| `basemap-baker` | `python:3.11-slim` (sentinel-tiles-baker) | `./assets/static/basemap:/out` | `build_offline_basemap.py` |
| `terrain-baker` | `python:3.11-slim` (sentinel-tiles-baker) | `./assets/static/terrain:/out` | `build_offline_terrain.py` |

The deleted services `dem-assets` and `osrm-assets` (and their `dem-assets/`, `osrm-assets/` directories) are removed entirely. The `assets` nginx image no longer bakes or rsyncs basemap/terrain; it bind-mounts `./assets/static/{basemap,terrain}` read-only.

## Why this is the right design

**Writes persist across interruptions.** Because bakers write to a host bind mount (not a BuildKit cache mount), a crash, OOM, or Ctrl-C leaves partial output on disk. Re-running the baker resumes: `build_offline_osrm.sh` uses `curl --continue-at -` for the PBF download; `build_offline_dem.py` and the tile scripts skip already-present files. No data is lost, no re-download is triggered.

**`osrm-extract` can use host swap.** Runtime containers are not cgroup-isolated from host swap the way BuildKit build steps are. On a 30 GB RAM + 64 GB swap host, `osrm-extract` can spill to swap and complete. Under BuildKit it was OOM-killed before finishing.

**No parallel-build contention.** Bakers run as standalone containers, not alongside 7 concurrent image builds. CPU and I/O are dedicated to the extract pipeline.

**Air-gap rule preserved (CLAUDE.md §8).** The `bake` profile is hidden from the default `docker compose up`. On an air-gapped deployment, the operator copies `./assets` and the image tarballs to the target host and runs `docker compose up -d` — zero network access, zero downloads.

**No cache debt.** The 1.1 TB of orphaned BuildKit blobs is reclaimed by `docker buildx prune -f` before the first baker run. New builds of the application images are fast (no large cache mounts) and idempotent.

**Files are root-owned but consistent.** Bakers run as root, so files written under `./assets` are root-owned — the same ownership as the old init containers. This is consistent and expected in the air-gap tarball/folder shipping workflow.

## Removed components

- `dem-assets/Dockerfile`, `dem-assets/scripts/entrypoint.sh` — replaced by `bakers/dem/Dockerfile`.
- `osrm-assets/Dockerfile`, `osrm-assets/scripts/entrypoint.sh` — replaced by `bakers/osrm/Dockerfile`.
- `assets/Dockerfile` basemap/terrain bake stages — basemap and terrain are now served via bind mount, not baked into the image.
- `depends_on: dem-assets` from `backend` and `worker`; `depends_on: osrm-assets` from `osrm` — runtime services no longer block on an init container; missing data produces honest 503 responses.
- `assets` healthcheck tile probes (`/basemap/0/0/0.png`, `/terrain/0/0/0.png`) — relaxed to `/healthz` + `/calibration/model_temperatures.json` so the stack becomes healthy before tiles are baked.

## Failure modes

| Condition | Outcome |
|---|---|
| Baker interrupted mid-run | Partial output persists; re-run resumes without re-downloading. |
| `osrm-extract` OOM on too-large region | Killed; PBF and partial outputs persist. Operator selects a smaller `PLANET_PBF_URL` and re-runs. |
| Runtime start with empty `./assets/osrm` | `osrm` service unhealthy; `/api/analytics/routes` returns 503. Stack still starts. |
| Runtime start with empty `./assets/dem` | `/api/analytics/viewshed` and `/api/analytics/los` return 503. Stack still starts. |
| Runtime start with no baked tiles | `assets` passes healthcheck via `/healthz`; basemap/terrain overlays absent in UI. Stack still starts. |

## Cross-references

- Superseded: [why-dem-osrm-as-sibling-baker-images.md](why-dem-osrm-as-sibling-baker-images.md)
- Related: [why-bake-reference-corpora-into-assets.md](why-bake-reference-corpora-into-assets.md) — fonts/reference/calibration remain baked into assets image
- [deployment/osrm-planet-bake.md](../deployment/osrm-planet-bake.md)
- [deployment/dem-glo30-bake.md](../deployment/dem-glo30-bake.md)
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
- [why-glo30-as-default-dem.md](why-glo30-as-default-dem.md)
- [why-osrm-replaced-networkx.md](why-osrm-replaced-networkx.md)
