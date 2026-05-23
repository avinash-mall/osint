# `scripts/build_offline_terrain.py` — Terrain Tile Pre-Fetch

**Path:** [scripts/build_offline_terrain.py](../../scripts/build_offline_terrain.py)

## Purpose

Pre-fetch OpenTopoMap raster tiles (z=0..14) into `assets/static/terrain/` for the air-gap deployment. Optional layer in the Geoint workspace. z=14 matches the frontend overlay autohide threshold ([why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)); past z=14 the imagery is the source of truth and the basemap layer is unmounted.

## Usage

```bash
# Full bake (default — z=0..14, ~22 GB, multiple days; OpenTopoMap rate-limits aggressively).
python scripts/build_offline_terrain.py

# Faster bake when block-level terrain isn't needed (z=0..10, ~80 MB).
python scripts/build_offline_terrain.py --zoom 0-10

# Smoke test.
python scripts/build_offline_terrain.py --zoom 0-4
```

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

## Cross-references

- [build-offline-basemap.md](build-offline-basemap.md)
- [decisions/why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
