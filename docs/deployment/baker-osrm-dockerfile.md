# `bakers/osrm/Dockerfile` — OSRM Runtime Baker

**Path:** [bakers/osrm/Dockerfile](../../bakers/osrm/Dockerfile)
**Lines:** ~21
**Depends on:** `ghcr.io/project-osrm/osrm-backend:v6.0.0`; `scripts/build_offline_osrm.sh`; env `PLANET_PBF_URL`, `DATA_DIR`, `PROFILE`

## Purpose

Runtime baker image for the planet OSRM MLD dataset. Runs under the `bake` Compose profile, downloads the OSM PBF, and executes the extract/partition/customize pipeline directly into the host-bind-mounted `./assets/osrm`. Replaces the former `osrm-assets` build-time init container.

## Why this design

BuildKit `RUN` cache mounts roll back on cancellation, which caused the planet PBF to be re-downloaded on every interrupted build attempt and left ~1.1 TB of orphaned cache. A runtime container writes directly to the host bind mount — interruptions leave partial output, and re-runs resume. `osrm-extract` can also use host swap (not available inside the build cgroup). See [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md).

## Key symbols

- [`build_offline_osrm.sh`](../../scripts/build_offline_osrm.sh#L1) — copied to `/app/build_offline_osrm.sh`; serves as the container ENTRYPOINT.
- `DATA_DIR=/data` — default output directory, overridden by Compose to `./assets/osrm`.
- `PROFILE=/opt/car.lua` — OSRM routing profile; the base image ships this at the expected path.

## Inputs / Outputs

- **Input:** `PLANET_PBF_URL` env var (Geofabrik or Geofabrik-compatible URL); `DATA_DIR` bind-mounted from `./assets/osrm`.
- **Output:** `planet.osrm*` MLD artifact set + `planet.osm.pbf` + `MANIFEST.sha256` in `./assets/osrm/`.

## Failure modes

- `osrm-extract` OOM on too-large region: killed by OS; PBF and partial outputs persist; operator selects a smaller `PLANET_PBF_URL`.
- PBF download interrupted: `curl --continue-at -` resumes on re-run.
- `osrm` runtime service absent data: fails healthcheck; backend returns 503 for routes.

## Cross-references

- [deployment/osrm-planet-bake.md](osrm-planet-bake.md) — operator runbook
- [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md)
- [decisions/why-osrm-replaced-networkx.md](../decisions/why-osrm-replaced-networkx.md)
- [backend/routing-osrm.md](../backend/routing-osrm.md)
