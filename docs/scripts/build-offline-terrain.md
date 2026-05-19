# `scripts/build_offline_terrain.py` — Terrain Tile Pre-Fetch

**Path:** [scripts/build_offline_terrain.py](../../scripts/build_offline_terrain.py)

## Purpose

Pre-fetch OpenTopoMap raster tiles (z=0..10) into `assets/static/terrain/` for the air-gap deployment. Optional layer in the Geoint workspace.

## Usage

```bash
python scripts/build_offline_terrain.py
```

Idempotent: existing tiles are skipped.

## Cross-references

- [build-offline-basemap.md](build-offline-basemap.md)
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
