# Sentinel

An open-source GEOINT exploitation platform that ingests satellite imagery and full-motion video, fuses detections into a graph ontology, and surfaces the picture through a dark-mode tactical workstation. Inference is consolidated on **SAM 3 / SAM 3.1** — open-vocabulary segmentation for RGB satellite, multispectral, and SAR imagery — plus **YOLOE-26x-seg** and SAM 3.1 PCS for FMV tracking.

The platform ships as a self-contained Docker Compose stack that can run fully air-gapped: every basemap tile, webfont, AI weight, worldwide Copernicus GLO-30 DEM (~150 GB), and planet-scale OSRM routing dataset (~150-200 GB) is baked at build time. Bulk geo data lands on the host at `./assets/dem/` and `./assets/osrm/` so it survives `docker system prune` and ships as plain folders.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  Nginx :3000  — single entry point                                     │
│  • / → frontend          • /api/, /ws → backend       • /tiles/ → titiler│
│  • /maps/ → martin       • /basemap/, /assets/ → assets  • /fmv/ → HLS │
├──────────────┬───────────────┬─────────────────────────────────────────┤
│  Frontend    │  Backend API  │  Inference (SAM 3 / 3.1 + YOLOE)         │
│  React 19    │  FastAPI      │  /detect (image) · /detect_video (FMV)   │
│  Vite 8      │  + WebSocket  │  /load · /unload  (profile pool)         │
├──────────────┴───────────────┴─────────────────────────────────────────┤
│  Celery worker (imagery + default queues) + worker_beat scheduler      │
├──────────┬───────────────┬──────────┬──────────┬───────────┬──────────┤
│  Neo4j   │  PostGIS      │  Redis   │  TiTiler │  Martin   │  Assets  │
│  graph   │  spatial + DB │  broker  │  COG     │  MVT      │  basemap │
└──────────┴───────────────┴──────────┴──────────┴───────────┴──────────┘
```

Only port **3000** is exposed to the host. Every other service runs on the internal compose network. Full service breakdown: [docs/architecture/system-overview.md](docs/architecture/system-overview.md).

## Tech Stack

| Layer | Technology |
|---|---|
| Graph DB | Neo4j 5.26 + APOC |
| Spatial DB | PostGIS 18-3.6 |
| Cache / broker | Redis 8 alpine |
| Backend | Python 3.11 · FastAPI · Uvicorn · Celery |
| Tile server | TiTiler 2.0.2 |
| Vector tiles | Martin 1.9.1 |
| AI inference | SAM 3 + SAM 3.1 PCS · YOLOE-26x-seg(-pf) · DINOv3 ViT-L SAT-493M · Prithvi-EO-2.0 · TerraMind v1 · DOTA-OBB · Grounding DINO (auto-gated) |
| Frontend | React 19 · TypeScript · Vite 8 · Tailwind · lucide-react |
| Map | react-leaflet (2D) · CesiumJS (optional 3D) |
| Auth | Signed session cookies (itsdangerous) · env-bootstrap admin · optional LDAP |
| Reverse proxy | Nginx alpine — TLS, tile cache (24 h TTL), HLS |
| Air-gap assets | nginx alpine + baked Carto Dark basemap (z=0..10) + IBM Plex; DEM + OSRM data on host bind mounts under `./assets/` |
| GPU | Turing → Blackwell (sm_75–sm_120) via per-host profiles |

---

## Quick Start

```bash
# 1. Detect host GPU + driver, write build settings to .env
python scripts/configure_host.py

# 2. Set HF_TOKEN in .env (required only when SAM3_WEIGHTS_SOURCE=official; gated)
echo "HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" >> .env

# 3. Strong session secret and admin password
echo "SESSION_SECRET=$(openssl rand -hex 32)"         >> .env
echo "ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')" >> .env

# 4. Build and start everything
docker compose up -d --build

# 5. Open the workstation
open http://localhost:3000
```

Sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`. Configure LDAP from **Admin → Sign-in & users** for multi-user deployments.

### First-build timing

The default `docker compose up -d --build` triggers worldwide bakes for two large datasets that back the map-page analytics. Plan disk space and wallclock accordingly, or pick a smaller option:

| Bake | What it does | First-build cost | Final on-host size |
|---|---|---|---|
| Code + basemap + AI weights | Frontend, backend, inference, Carto z=0–10 tiles, fonts | ~30-90 min | ~30 GB image layers |
| **DEM** (Copernicus GLO-30) | Downloads ~26,000 worldwide 1° tiles + builds `glo30.vrt` | ~6-24 h, ~150 GB | `./assets/dem/` ~150 GB |
| **OSRM** (planet routing) | Downloads planet PBF + `osrm-extract`/`partition`/`customize` | ~10-30 h, ~250 GB peak (80 GB PBF + 150-200 GB artifacts) | `./assets/osrm/` ~200 GB |

After the first build, the DEM and OSRM data live on the host at `./assets/dem/` and `./assets/osrm/` and are read by the backend / OSRM container via bind mounts. They survive `docker system prune` and ship as plain folders (`tar` the directory, copy, untar on the target — no `docker save` of the baker images needed).

Subsequent `docker compose up -d --build` runs are seconds — BuildKit's layer cache hits the bake `RUN` step and the entrypoints see the host folder already matches `MANIFEST.sha256` and skip the rsync.

### Faster / smaller bakes

If you don't need worldwide coverage on the first build, use one of these options at the same `docker compose up -d --build` step:

```bash
# Regional DEM (~10-30 min) — example: Gulf states
DEM_LAT_MIN=20 DEM_LAT_MAX=35 DEM_LON_MIN=50 DEM_LON_MAX=65 \
  docker compose up -d --build

# Regional OSRM (~30-90 min) — example: any Geofabrik extract
PLANET_PBF_URL=https://download.geofabrik.de/north-america-latest.osm.pbf \
  docker compose up -d --build

# Skip a bake entirely — endpoint returns honest 503 until you re-bake
DEM_ENABLED=0  docker compose up -d --build      # skip DEM (no viewshed/LOS)
OSRM_ENABLED=0 docker compose up -d --build      # skip OSRM (no routing)
```

Full reference: [docs/deployment/dem-glo30-bake.md](docs/deployment/dem-glo30-bake.md) and [docs/deployment/osrm-planet-bake.md](docs/deployment/osrm-planet-bake.md).

> **Deployment banner:** the login screen shows `DEMO BUILD · NOT FOR OPERATIONAL USE` by default. Set `SENTINEL_DEPLOYMENT_MODE=internal|accredited` (and optionally `SENTINEL_DEPLOYMENT_LABEL`) in `.env` to change it — a stock clone has no accreditation to imply otherwise.

> **LLM (Ava):** point `.env` → `OPENAI_API_BASE` at a local vLLM / Ollama instance. Without it, LLM-backed features return a graceful 503; everything else works offline. See [docs/operations/llm-ava-configuration.md](docs/operations/llm-ava-configuration.md).

> **Air-gap target?** See [docs/deployment/offline-airgap-deployment.md](docs/deployment/offline-airgap-deployment.md). Bake on a connected host, then ship `./assets/dem/`, `./assets/osrm/`, and the `docker save`-d images to the target.

---

## Documentation

This README is intentionally short. **All detailed reference lives under [docs/](docs/).**

- **One-line index of every doc:** [docs/INDEX.txt](docs/INDEX.txt) (pipe-delimited, ~125 entries)
- **Docs landing page:** [docs/README.md](docs/README.md)
- **For coding agents:** [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), [.cursor/rules](.cursor/rules)

### High-value entry points

| Topic | Doc |
|---|---|
| System topology | [docs/architecture/system-overview.md](docs/architecture/system-overview.md) |
| Full API reference (~100 routes) | [docs/backend/api-routes-reference.md](docs/backend/api-routes-reference.md) |
| Imagery ingest pipeline | [docs/architecture/data-flow-imagery.md](docs/architecture/data-flow-imagery.md) |
| FMV ingest pipeline | [docs/architecture/data-flow-fmv.md](docs/architecture/data-flow-fmv.md) |
| Inference service overview | [docs/inference/service-overview.md](docs/inference/service-overview.md) |
| Environment variables (full reference) | [docs/deployment/environment-variables-reference.md](docs/deployment/environment-variables-reference.md) |
| Docker compose services | [docs/deployment/docker-compose-services.md](docs/deployment/docker-compose-services.md) |
| GPU profile detection | [docs/deployment/gpu-profile-detection.md](docs/deployment/gpu-profile-detection.md) |
| Offline / air-gap deployment | [docs/deployment/offline-airgap-deployment.md](docs/deployment/offline-airgap-deployment.md) |
| Worldwide DEM bake (GLO-30) | [docs/deployment/dem-glo30-bake.md](docs/deployment/dem-glo30-bake.md) |
| Planet OSRM bake | [docs/deployment/osrm-planet-bake.md](docs/deployment/osrm-planet-bake.md) |
| Inference layer benchmarks | [docs/benchmarks/inference-layer-comparison.md](docs/benchmarks/inference-layer-comparison.md) |
| Why open-vocabulary | [docs/decisions/why-open-vocabulary.md](docs/decisions/why-open-vocabulary.md) |
| Why YOLOE replaced AMG | [docs/decisions/why-yoloe-replaced-amg.md](docs/decisions/why-yoloe-replaced-amg.md) |
| Adding a new detection model | [docs/conventions/adding-a-new-detection-model.md](docs/conventions/adding-a-new-detection-model.md) |
| Authentication & LDAP setup | [docs/operations/auth-and-ldap-setup.md](docs/operations/auth-and-ldap-setup.md) |

---

## Development

```bash
# Frontend (hot reload — talks to a running backend at :8080)
cd frontend && npm install && npm run dev

# Backend (auto-reload)
cd backend && uvicorn main:app --reload --port 8080

# Celery worker
cd backend && celery -A worker.celery_app worker -Q imagery,default --loglevel=info

# Frontend production build (TypeScript check + Vite bundle)
cd frontend && npm run build

# Re-seed DB ontology from the JSON snapshot
python backend/scripts/seed_ontology.py
```

For day-to-day inference iteration, layer a `docker-compose.dev.yml` with a writable `sam3_models` volume — see [docs/deployment/offline-airgap-deployment.md](docs/deployment/offline-airgap-deployment.md#dev-override).

---

## Licenses

| Component | License | Gating |
|---|---|---|
| SAM 3 / SAM 3.1 code + weights | [Meta SAM License](https://github.com/facebookresearch/sam3/blob/main/LICENSE) | **Gated** (or use the `1038lab/sam3` mirror) |
| DINOv3 weights | [Meta DINOv3 License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/) | **Gated** |
| YOLOE weights | AGPL-3.0 | Open |
| Prithvi-EO-2.0 weights | Apache 2.0 | Open |
| TerraMind v1 weights | Apache 2.0 | Open |
| Grounding-DINO weights | Apache 2.0 | Open |
| Carto basemap tiles | © OpenStreetMap contributors · © CARTO (CC-BY) | Attribution required |
| IBM Plex fonts | SIL OFL 1.1 | Served at `/assets/LICENSE.txt` |
