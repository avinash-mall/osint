# `scripts/build_offline_basemap.py` — Carto Dark Pre-Fetch

**Path:** [scripts/build_offline_basemap.py](../../scripts/build_offline_basemap.py)

## Purpose

Pre-fetch Carto Dark Matter raster tiles for zoom levels 0..14 into `assets/static/basemap/` → the air-gap deployment serves them read-only. z=14 is the analyst's reference-overlay scale ceiling; past z=14 the imagery is the source of truth and the frontend unmounts the basemap layer (see [why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)).

## Runtime container (recommended)

This script now runs inside the **`basemap-baker`** Compose service (profile `bake`), which bind-mounts `./assets/static/basemap` into the container at `/out` and passes the appropriate `--out` and `--zoom` flags. Output lands directly on the host filesystem.

```bash
# Full bake via the baker container (z=0..14, ~13 GB, overnight)
docker compose --profile bake up basemap-baker

# Override zoom range
BASEMAP_ZOOM=0-8 docker compose --profile bake up basemap-baker
```

The baker image is `sentinel-tiles-baker` (built from `bakers/tiles/Dockerfile`, shared with `terrain-baker`). The `assets` nginx image no longer bakes basemap tiles; it bind-mounts `./assets/static/basemap` read-only.

## Direct usage (connected dev host)

```bash
# Full bake (default — z=0..14, ~13 GB, overnight on a fast connection).
python scripts/build_offline_basemap.py --out assets/static/basemap

# Faster smoke bake (z=0..8, ~50 MB, minutes).
python scripts/build_offline_basemap.py --out assets/static/basemap --zoom 0-8

# Single zoom level.
python scripts/build_offline_basemap.py --out assets/static/basemap --zoom 6
```

Idempotent: tiles already on disk are skipped, so a crashed run resumes with the same command.

## Sizes (approximate, full world)

| Zoom range | Tile count | On-disk     |
| ---------- | ---------: | ----------: |
| 0..10      | ~1.4 M     | ~50 MB      |
| 0..12      | ~22 M      | ~800 MB     |
| **0..14**  | **~358 M** | **~13 GB**  |

## Output

- `assets/static/basemap/{z}/{x}/{y}.png` (~13 GB at z=0..14)
- License + attribution file at `assets/static/LICENSE.txt`

The `assets` nginx service bind-mounts `./assets/static/basemap` read-only into its HTML root and serves these tiles over HTTP. The healthcheck for the `assets` service only probes `/healthz` — the stack becomes healthy before tiles are baked; the basemap overlay is simply absent in the UI until the baker completes.

Treat `assets/static/basemap/` as **read-only** from agents — never write here outside running this script or the baker. See [AGENTS.md](../../AGENTS.md).

## Cross-references

- [decisions/why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)
- [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md)
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
- [scripts/build-offline-terrain.md](build-offline-terrain.md)
