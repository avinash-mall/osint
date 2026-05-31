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
| `assets` | `sentinel-assets:offline` | internal 80 | fonts, reference-corpora, calibration; binds `./assets/static/{basemap,terrain}` RO |
| `llm-local-proxy` *(profile `llm-proxy`)* | `alpine/socat:1.8.0.3` | host `127.0.0.1:18001` | optional loopback-only socat forwarder |
| `osrm-baker` *(profile `bake`)* | `sentinel-osrm-baker` | — | runtime baker; writes OSRM MLD into `./assets/osrm`; exits 0 |
| `dem-baker` *(profile `bake`)* | `sentinel-dem-baker` | — | runtime baker; writes GLO-30 tiles into `./assets/dem`; exits 0 |
| `basemap-baker` *(profile `bake`)* | `sentinel-tiles-baker` | — | runtime baker; writes Carto Dark tiles into `./assets/static/basemap`; exits 0 |
| `terrain-baker` *(profile `bake`)* | `sentinel-tiles-baker` | — | runtime baker; writes OpenTopoMap tiles into `./assets/static/terrain`; exits 0 |

## Why this layout

- **Only nginx exposed** — all inter-service traffic on the internal bridge. Air-gap-friendly.
- **Worker + worker_beat share the backend image**, run different commands — saves a build, keeps shared code in sync.
- **Inference is its own image** — CUDA stack is heavy (~14 GB image), unrelated to the backend's Python runtime.
- **Auth secrets fail fast** — `backend` requires `ADMIN_PASSWORD` and `SESSION_SECRET` from `.env`; no checked-in fallback starts the API.
- **`llm-local-proxy` is a separate compose profile** (only started with `--profile llm-proxy`) — a loopback-bound `socat` forwarder so containers can reach a host-side vLLM/Ollama bound to `127.0.0.1`.

## Named volumes

| Volume | RW writer | RO readers | Purpose |
|---|---|---|---|
| `neo4j_data` | `neo4j` | — | Graph DB persistence |
| `pg_data` | `postgis` | — | Relational DB persistence |
| `imagery_data` | `backend`, `worker` | `titiler`, `inference-sam3` | COGs, chips |
| `fmv_data` | `backend`, `worker` | `nginx`, `inference-sam3` | FMV clips + HLS |
| `dataset_data` | `backend`, `worker` | — | Training-set storage |
| `reference_corpora_data` | `assets` | `backend`, `worker` | Reference chips bake (mounts at `/opt/reference-corpora`) |
| `calibration_data` | `assets` | `backend`, `worker` | Per-detector temperatures bake (mounts at `/data/calibration`) |
| `./assets/dem` (host bind) | `dem-baker` (`bake` profile) | `backend`, `worker` | GLO-30 DEM mosaic; read-only at runtime |
| `./assets/osrm` (host bind) | `osrm-baker` (`bake` profile) | `osrm` | Planet OSRM MLD dataset; read-only at runtime |
| `./assets/static/basemap` (host bind) | `basemap-baker` (`bake` profile) | `assets` | Carto Dark tiles; read-only at runtime |
| `./assets/static/terrain` (host bind) | `terrain-baker` (`bake` profile) | `assets` | OpenTopoMap tiles; read-only at runtime |

`reference_corpora_data` and `calibration_data` follow the bake-then-rsync pattern: the `assets` image holds the canonical content at an un-mounted staging path (`/opt/baked-reference-chips/`, `/opt/baked-calibration/`); the assets entrypoint rsyncs onto the named volume on every container start, digest-gated by `MANIFEST.sha256`. See [operations/calibration-shipping.md](../operations/calibration-shipping.md).

The four geo-asset bind mounts (`./assets/dem`, `./assets/osrm`, `./assets/static/basemap`, `./assets/static/terrain`) are written by the `bake`-profile baker services and read read-only by the runtime stack. See [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md).

## Cross-references

- [architecture/service-topology.md](../architecture/service-topology.md)
- [nginx-gateway-and-tile-cache.md](nginx-gateway-and-tile-cache.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
- [volume-mounts-and-paths.md](volume-mounts-and-paths.md)
- [environment-variables-reference.md](environment-variables-reference.md)
- [../decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
- [../operations/reference-corpora-bake.md](../operations/reference-corpora-bake.md)
- [../operations/calibration-shipping.md](../operations/calibration-shipping.md)
