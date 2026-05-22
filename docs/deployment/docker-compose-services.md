# Docker Compose Services

**Source:** [docker-compose.yml](../../docker-compose.yml)

## Services

| Service | Image | Port | Notes |
|---|---|---|---|
| `nginx` | `sentinel-nginx:offline` | **3000:80** (only exposed) | TLS, tile cache, FMV HLS |
| `frontend` | `sentinel-frontend:latest` | internal 3000 | Vite-built React SPA |
| `backend` | `sentinel-backend:latest` | internal 8080 | FastAPI + WebSocket |
| `worker` | `sentinel-backend:latest` | — | Celery worker (queues: imagery, default) |
| `worker_beat` | `sentinel-backend:latest` | — | Celery beat scheduler |
| `inference-sam3` | `sentinel-inference-sam3:gpu` | internal 8001 | GPU container |
| `neo4j` | `neo4j:5.26.26-community-ubi10` | internal 7687/7474 | APOC enabled |
| `postgis` | `postgis/postgis:18-3.6` | internal 5432 | |
| `redis` | `redis:8-alpine` | internal 6379 | Celery broker |
| `titiler` | `ghcr.io/developmentseed/titiler:2.0.2` | internal 8080 | COG tile server |
| `martin` | `ghcr.io/maplibre/martin:1.9.1` | internal 3000 | PostGIS → MVT |
| `assets` | `sentinel-assets:offline` | internal 80 | offline basemap + fonts |
| `llm-local-proxy` *(profile `llm-proxy`)* | `alpine/socat:1.8.0.3` | host 18001 | optional socat forwarder |

## Why this layout

- **Only nginx exposed** — all inter-service traffic on the internal bridge. Air-gap-friendly.
- **Worker + worker_beat share the backend image**, run different commands — saves a build, keeps shared code in sync.
- **Inference is its own image** — CUDA stack is heavy (~14 GB image), unrelated to the backend's Python runtime.
- **`llm-local-proxy` is a separate compose profile** (only started with `--profile llm-proxy`) — a `socat` forwarder so containers can reach a host-side vLLM/Ollama bound to `127.0.0.1`.

## Cross-references

- [architecture/service-topology.md](../architecture/service-topology.md)
- [nginx-gateway-and-tile-cache.md](nginx-gateway-and-tile-cache.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
- [volume-mounts-and-paths.md](volume-mounts-and-paths.md)
