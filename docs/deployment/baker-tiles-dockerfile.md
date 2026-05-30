# `bakers/tiles/Dockerfile` — Tiles Runtime Baker (basemap + terrain)

**Path:** [bakers/tiles/Dockerfile](../../bakers/tiles/Dockerfile)
**Lines:** ~13
**Depends on:** `python:3.11-slim`; `scripts/build_offline_basemap.py`; `scripts/build_offline_terrain.py`; env `BASEMAP_ZOOM`, `BASEMAP_CONCURRENCY`, `TERRAIN_ZOOM`, `TERRAIN_CONCURRENCY`

## Purpose

Shared runtime baker image for the Carto Dark basemap tile pyramid and the OpenTopoMap terrain tile pyramid. One image, two Compose services (`basemap-baker`, `terrain-baker`), each selecting a different script + output path via the Compose `command:`. Runs under the `bake` Compose profile; output lands directly in `./assets/static/{basemap,terrain}` on the host. The `assets` nginx image no longer bakes these tiles — it bind-mounts the directories read-only.

## Why this design

Sharing one slim Python image for both tile fetchers avoids a duplicate base layer (both scripts use only the stdlib). No `CMD` is set — the Compose `command:` controls which script runs and with which zoom range, allowing one image build to serve both `basemap-baker` and `terrain-baker`. Writes go directly to the host bind mount, surviving interruptions without data loss. See [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md).

## Key symbols

- [`build_offline_basemap.py`](../../scripts/build_offline_basemap.py#L1) — copied to `/app/`; used by `basemap-baker` service.
- [`build_offline_terrain.py`](../../scripts/build_offline_terrain.py#L1) — copied to `/app/`; used by `terrain-baker` service.

## Inputs / Outputs

- **`basemap-baker`:** `BASEMAP_ZOOM` (default `0-14`), `BASEMAP_CONCURRENCY`; bind-mount `./assets/static/basemap:/out`. Output: `{z}/{x}/{y}.png` + `LICENSE.txt`.
- **`terrain-baker`:** `TERRAIN_ZOOM` (default `0-14`), `TERRAIN_CONCURRENCY` (default 4); bind-mount `./assets/static/terrain:/out`. Output: `{z}/{x}/{y}.png` + `ATTRIBUTION.txt`.

## Failure modes

- Tiles already present are skipped (idempotent); interrupted runs resume.
- OpenTopoMap rate-limits heavily at high concurrency; keep `TERRAIN_CONCURRENCY ≤ 4`.
- Missing basemap/terrain at runtime: `assets` healthcheck passes via `/healthz`; tile overlays absent in UI; stack still starts.

## Cross-references

- [scripts/build-offline-basemap.md](../scripts/build-offline-basemap.md)
- [scripts/build-offline-terrain.md](../scripts/build-offline-terrain.md)
- [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md)
- [decisions/why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)
- [deployment/offline-airgap-deployment.md](offline-airgap-deployment.md)
