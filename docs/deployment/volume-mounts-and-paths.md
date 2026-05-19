# Volume Mounts & Paths

## Inside the container

| Path | Purpose | Source binding |
|---|---|---|
| `/data/imagery/` | COGs, chips, incoming uploads | `${IMAGERY_PATH:-./data/imagery}` |
| `/data/imagery/incoming/` | Untouched uploads awaiting ingest | — |
| `/data/imagery/processed/` | COGs ready for titiler | — |
| `/data/imagery/chips/<pass_id>/` | Chip PNGs/GeoTIFFs per pass | — |
| `/data/fmv/` | FMV clip uploads + HLS segments | `${FMV_PATH:-./data/fmv}` |
| `/data/fmv/<clip_id>/playlist.m3u8` | HLS playlist served by nginx | — |
| `/data/datasets/` | Training-set storage | `${DATASET_PATH:-./data/datasets}` |
| `/data/dem/dem.tif` | DEM for viewshed/LOS analytics | `${DEM_PATH:-./data/dem/dem.tif}` |
| `/data/routing/graph.pkl` | osmnx graph for routing | `${ROUTING_GRAPH_PATH:-./data/routing/graph.pkl}` |
| `/data/calibration/model_temperatures.json` | Per-model temperatures | — |

## Service-specific

| Path | Used by | Purpose |
|---|---|---|
| `/var/cache/nginx/` | nginx | Tile cache (24 h TTL, 2 GB max) |
| `/root/.cache/huggingface/` | inference-sam3 | Model weights (baked at build; bind-mountable in dev) |
| `/var/lib/postgresql/data/` | postgis | DB persistence |
| `/data/` (neo4j volume) | neo4j | Graph persistence |

## Read-only paths (treat as immutable from the dev host)

These are populated at build time or by long-running pipelines — agents should not write:

- `bench/` — benchmark output JSON
- `assets/static/basemap/` — pre-built basemap tiles
- `inference-sam3/yolo*.pt`, `inference-sam3/yoloe-*.pt`, `inference-sam3/mobileclip2_b.ts` — bundled weights

## Cross-references

- [docker-compose-services.md](docker-compose-services.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [backend/routing-graph-osmnx.md](../backend/routing-graph-osmnx.md)
