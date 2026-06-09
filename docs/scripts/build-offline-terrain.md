# `scripts/build_offline_terrain.py` — Terrain Tile Pre-Fetch

**Path:** [scripts/build_offline_terrain.py](../../scripts/build_offline_terrain.py)

## Purpose

Pre-fetch OpenTopoMap raster tiles (z=0..14) into `assets/static/terrain/` for the air-gap deployment. Optional analytics overlay in the Geoint workspace. z=14 matches the frontend overlay autohide threshold ([why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)); past z=14 the imagery is the source of truth and the basemap layer is unmounted.

## Runtime container (recommended)

This script now runs inside the **`terrain-baker`** Compose service (profile `bake`), which bind-mounts `./assets/static/terrain` into the container at `/out` and passes the appropriate `--out` and `--zoom` flags. Output lands directly on the host filesystem.

```bash
# Full bake via the baker container (z=0..14, ~22 GB, multiple days)
docker compose --profile bake up terrain-baker

# Override zoom range (z=0..10, ~80 MB, much faster)
TERRAIN_ZOOM=0-10 docker compose --profile bake up terrain-baker
```

The baker image is `sentinel-tiles-baker` (built from `bakers/tiles/Dockerfile`, shared with `basemap-baker`). The `assets` nginx image no longer bakes terrain tiles; it bind-mounts `./assets/static/terrain` read-only.

## Direct usage (connected dev host)

```bash
# Full bake (default — z=0..14, ~22 GB, multiple days; OpenTopoMap rate-limits aggressively).
python scripts/build_offline_terrain.py --out assets/static/terrain

# Faster bake when block-level terrain isn't needed (z=0..10, ~80 MB).
python scripts/build_offline_terrain.py --out assets/static/terrain --zoom 0-10

# Smoke test.
python scripts/build_offline_terrain.py --out assets/static/terrain --zoom 0-4
```

**Memory bound:** like the basemap baker, tiles are fetched through a bounded sliding window of in-flight futures (`max(concurrency*4, 64)`) rather than submitting every `4**z` future up front (which OOMs at high zoom). See [build-offline-basemap.md](build-offline-basemap.md).

Idempotent: existing tiles are skipped, so a crashed/throttled run resumes with the same command. Default `--concurrency 4` is intentionally low to respect OpenTopoMap's volunteer-hosted policy — don't raise it.

## Sizes (approximate, full world)

| Zoom range | Tile count | On-disk     |
| ---------- | ---------: | ----------: |
| 0..10      | ~1.4 M     | ~80 MB      |
| 0..12      | ~22 M      | ~1.3 GB     |
| **0..14**  | **~358 M** | **~22 GB**  |

## Output

- `assets/static/terrain/{z}/{x}/{y}.png`
- `assets/static/terrain/ATTRIBUTION.txt`

The `assets` nginx service bind-mounts `./assets/static/terrain` read-only into its HTML root and serves these tiles over HTTP. The healthcheck for the `assets` service only probes `/healthz` — the stack becomes healthy before tiles are baked; the terrain overlay is simply absent in the UI until the baker completes.

## Cross-references

- [build-offline-basemap.md](build-offline-basemap.md)
- [decisions/why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)
- [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md)
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
