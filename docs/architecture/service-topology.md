# Service Topology

**Source:** [docker-compose.yml](../../docker-compose.yml)

## Purpose

Per-service inventory: image, ports, depends-on, volumes. Read with the compose file open.

## Services

| Service | Image | Port | Depends on | Notes |
|---|---|---|---|---|
| `nginx` | `sentinel-nginx:offline` | **3000:80** (only host-exposed) | frontend, backend, titiler, martin, assets | TLS termination, 24h tile cache, FMV HLS routing |
| `frontend` | `sentinel-frontend:latest` | internal 3000 | — | Vite-built React SPA, served by in-image nginx |
| `backend` | `sentinel-backend:latest` | internal 8080 | neo4j, postgis, redis | FastAPI + WebSocket |
| `worker` | `sentinel-backend:latest` | — | neo4j, postgis, redis, inference-sam3 | Celery worker; queues `imagery`, `default` |
| `worker_beat` | `sentinel-backend:latest` | — | redis | Celery beat scheduler (periodic feed polling, cleanup) |
| `inference-sam3` | `sentinel-inference-sam3:gpu` | internal 8001 | — | GPU image; SAM3+SAM3.1+YOLOE+DINOv3+Prithvi+TerraMind+DOTA+GDINO |
| `neo4j` | `neo4j:5.26.26-community-ubi10` | internal 7687/7474 | — | APOC enabled |
| `postgis` | `postgis/postgis:18-3.6` | internal 5432 | — | spatial catalog, detections, auth, ontology |
| `redis` | `redis:8-alpine` | internal 6379 | — | Celery broker |
| `titiler` | `ghcr.io/developmentseed/titiler:2.0.2` | internal 8080 | — | on-the-fly COG tile server |
| `martin` | `ghcr.io/maplibre/martin:1.9.1` | internal 3000 | postgis | PostGIS → MVT vector tiles |
| `assets` | `sentinel-assets:offline` | internal 80 | — | offline Carto Dark + OpenTopoMap basemap (z=0..14), IBM Plex fonts |
| `llm-local-proxy` *(profile `llm-proxy`)* | `alpine/socat:1.8.0.3` | host 18001 | — | optional TCP forwarder for host-side vLLM/Ollama |

## Shared volumes

| Volume / bind | Mounted by | Path inside container | Purpose |
|---|---|---|---|
| `imagery_data` (bind: `${IMAGERY_PATH:-./data/imagery}`) | backend, worker, titiler | `/data/imagery` | COG storage, chips, incoming uploads |
| `fmv_data` (bind: `${FMV_PATH:-./data/fmv}`) | backend, worker, nginx | `/data/fmv` | uploads + HLS segments |
| `dataset_data` (bind: `${DATASET_PATH:-./data/datasets}`) | backend, worker | `/data/datasets` | training datasets |
| `dem_data` | backend | `/data/dem` | DEM GeoTIFF for viewshed/LOS analytics |
| `routing_data` | backend | `/data/routing` | pickled `osmnx` graph |
| `sam3_models` | inference-sam3 | `/root/.cache/huggingface` | model weight cache (bind-mounted in dev) |
| `neo4j_data` | neo4j | `/data` | graph persistence |
| `postgis_data` | postgis | `/var/lib/postgresql/data` | DB persistence |

## Network

Single bridge network (`sentinel_default`). Internal DNS resolves service names (`backend`, `inference-sam3`, `postgis`, …). All `--internal` in offline builds — see [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md).

## Cross-references

- [deployment/docker-compose-services.md](../deployment/docker-compose-services.md) — service-by-service compose reference
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md) — nginx route table
- [deployment/volume-mounts-and-paths.md](../deployment/volume-mounts-and-paths.md)
