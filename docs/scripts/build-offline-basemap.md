# `scripts/build_offline_basemap.py` — Carto Dark Pre-Fetch

**Path:** [scripts/build_offline_basemap.py](../../scripts/build_offline_basemap.py)

## Purpose

Pre-fetch Carto Dark Matter raster tiles for zoom levels 0..14 into `assets/static/basemap/` → the offline image can be built without runtime network access. z=14 is the analyst's reference-overlay scale ceiling; past z=14 the imagery is the source of truth and the frontend unmounts the basemap layer (see [why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)).

## Usage

```bash
# Full bake (default — z=0..14, ~13 GB, overnight on a fast connection).
python scripts/build_offline_basemap.py

# Faster smoke bake (z=0..8, ~50 MB, minutes).
python scripts/build_offline_basemap.py --zoom 0-8

# Single zoom level.
python scripts/build_offline_basemap.py --zoom 6
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

Treat `assets/static/basemap/` as **read-only** from agents — never write here outside running this script. See [AGENTS.md](../../AGENTS.md).

## Cross-references

- [decisions/why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
