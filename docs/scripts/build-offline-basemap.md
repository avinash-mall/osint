# `scripts/build_offline_basemap.py` — Carto Dark Pre-Fetch

**Path:** [scripts/build_offline_basemap.py](../../scripts/build_offline_basemap.py)

## Purpose

Pre-fetch Carto Dark Matter raster tiles for zoom levels 0..10 into `assets/static/basemap/` so the offline image can be built without network access at runtime.

## Usage

```bash
python scripts/build_offline_basemap.py
# Optional: limit zoom levels for faster iteration
python scripts/build_offline_basemap.py --max-zoom 8
```

Idempotent: tiles already on disk are skipped.

## Output

- `assets/static/basemap/{z}/{x}/{y}.png` (~3 GB at z=0..10)
- License + attribution file at `assets/static/LICENSE.txt`

Treat `assets/static/basemap/` as **read-only** from agents — never write here outside of running this script. See [AGENTS.md](../../AGENTS.md).

## Cross-references

- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
