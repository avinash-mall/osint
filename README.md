# Gotham GEOINT Platform

An open-source GEOINT exploitation platform inspired by Palantir Gotham. Ingests satellite imagery, fuses detections into a graph ontology, and surfaces the picture through a dark-mode tactical dashboard.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Nginx  :8090  (tile cache proxy + FMV HLS static files)     │
├──────────────┬───────────────┬──────────────────────────────-┤
│  Frontend    │  Backend API  │  Inference                     │
│  React 19    │  FastAPI      │  YOLOv8n + SAHI               │
│  :3000       │  :8080        │  :8001                         │
├──────────────┴───────────────┴────────────────────────────────┤
│  Celery worker (imagery + default queues)                     │
├──────────┬───────────────┬──────────┬──────────┬─────────────┤
│  Neo4j   │  PostGIS      │  Redis   │  TiTiler │  Martin     │
│  :7474   │  :5432        │  :6379   │  :8081   │  :3001      │
│  :7687   │               │          │  (COG)   │  (MVT)      │
└──────────┴───────────────┴──────────┴──────────┴─────────────┘
```

**10 services** — all containerised, all healthy on an air-gapped host.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Graph DB | Neo4j 5.20 + APOC |
| Spatial DB | PostGIS 16-3.4 |
| Backend | Python 3.11 / FastAPI / Uvicorn |
| Task queue | Celery + Redis alpine (queues: `imagery`, `default`) |
| Tile server | TiTiler — Cloud-Optimised GeoTIFF on-the-fly |
| Vector tiles | Martin — PostGIS → Mapbox Vector Tiles |
| AI inference | YOLOv8n + SAHI, CPU-only |
| Frontend | React 19 · TypeScript · Vite 8 · Tailwind CSS v4 |
| Map | react-leaflet (2D) · react-globe.gl (3D globe) · CesiumJS 1.124 (3D terrain) |
| Reverse proxy | Nginx alpine — tile cache (24 h TTL) + FMV HLS serving |

---

## Quick Start

```bash
# 1. Build all images
docker compose build

# 2. Start the full stack
docker compose up -d

# 3. Wait for databases to be healthy (~30 s), then seed
docker exec -it osint-backend-1 python seed.py         # Neo4j ontology
docker exec -it osint-backend-1 python seed_postgis.py # PostGIS passes + detections
docker exec -it osint-backend-1 python add_targets.py  # HPTL targets
docker exec -it osint-backend-1 python add_constellation.py  # Satellite constellation

# 4. Open the dashboard
open http://localhost:3000
```

> **LLM (Ava):** point `.env` → `OPENAI_API_BASE` at a local vLLM / Ollama instance.
> Without it the Ava chat tab returns a graceful error — all other tabs work offline.

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://neo4j:7687` | Graph database |
| `NEO4J_USERNAME` | `neo4j` | |
| `NEO4J_PASSWORD` | `password` | |
| `POSTGIS_URI` | `postgresql://gotham:gotham@postgis:5432/gotham` | Spatial database |
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker |
| `TITILER_URL` | `http://titiler:8080` | Internal tile server |
| `INFERENCE_URL` | `http://inference:8001` | Internal inference service |
| `IMAGERY_PATH` | `/data/imagery` | Shared volume mount |
| `OPENAI_API_BASE` | *(unset)* | Local LLM endpoint |
| `OPENAI_API_KEY` | `dummy` | |
| `OPENAI_MODEL` | `google/gemma-4-31B-it` | |

---

## Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `neo4j` | `neo4j:5.20.0` | 7474 / 7687 | Graph ontology + APOC |
| `postgis` | `postgis/postgis:16-3.4` | 5432 | Spatial catalog, detections |
| `backend` | `gotham-backend:latest` | 8080 | REST API |
| `worker` | `gotham-backend:latest` | — | Celery imagery worker |
| `frontend` | `gotham-frontend:latest` | 3000 | Vite dev server |
| `titiler` | `developmentseed/titiler:latest` | 8081 | COG tile server |
| `martin` | `maplibre/martin:latest` | 3001 | PostGIS → MVT |
| `inference` | `gotham-inference:latest` | 8001 | YOLOv8n detection |
| `redis` | `redis:alpine` | 6379 | Task queue |
| `nginx` | `nginx:alpine` | 8090 | Tile cache + FMV HLS |

---

## Frontend Modules

The dashboard is a single-page application with a sidebar of 7 tabs.

| Tab | Component | What it shows |
|-----|-----------|---------------|
| **Graph** | Ontology Explorer | Force-directed graph of all Neo4j nodes (Targets, Assets, Observations, Satellites, Bases, LaunchPoints) and their relationships, rendered with `react-force-graph-2d` |
| **Map** | Gaia Geospatial | `react-leaflet` map with CARTO Dark Matter basemap, TiTiler satellite imagery overlay (opacity slider), AI detection GeoJSON overlay colour-coded by class, asset track polylines, base/launch-point markers, time slider and layer panel |
| **Targets** | Target Workbench | High-Priority Target List — status badges, inline status updates (`PUT /api/targets/{id}/status`), detection history panel, satellite pass trigger |
| **Space** | Constellation View | `react-globe.gl` 3D globe with satellite point cloud, orbital arc overlays, and per-satellite collection window panel drawn from `/api/constellation` |
| **Browser** | Data Browser | Tabular view of raw graph nodes and telemetry from `/api/graph`; sortable columns |
| **Ava** | Cognitive Engine | Natural-language chat → `GraphCypherQAChain` (LangChain) → Neo4j Cypher → answer; shows "LLM OFFLINE" when no endpoint is configured |
| **3D** | View3D (CesiumJS) | CesiumJS 1.124 globe with offline NaturalEarth II TMS basemap via `CESIUM_BASE_URL='/cesium/'`; FMV clip integration hook |

---

## Imagery Pipeline

### Ingest a GeoTIFF

```bash
# Drop a raw raster into the incoming volume (or use a full path inside the container)
curl -X POST http://localhost:8080/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"image_url": "/data/imagery/incoming/sentinel2.tif", "sensor_type": "Optical"}'
```

The `imagery` Celery worker then:

1. Converts the raster to a Cloud-Optimised GeoTIFF (COG) via `gdal_translate`
2. Catalogs the pass in PostGIS with a `MULTIPOLYGON` footprint
3. Creates a `SatellitePass` node in Neo4j
4. Slices the COG into 640×640 uint8 PNG chips
5. Sends each chip to the inference service (`POST /detect`)
6. Georeferences bounding boxes back to Lat/Lon
7. Stores detections in PostGIS and Neo4j

### Tile URLs

```
# COG tiles (TiTiler — direct)
http://localhost:8081/cog/tiles/{z}/{x}/{y}?url=/data/imagery/processed/pass_cog.tif

# COG tiles (Nginx cache proxy — 24 h TTL)
http://localhost:8090/cog/tiles/{z}/{x}/{y}?url=/data/imagery/processed/pass_cog.tif

# Vector tiles (Martin)
http://localhost:3001/detections/{z}/{x}/{y}
http://localhost:3001/satellite_passes/{z}/{x}/{y}
http://localhost:3001/ne_countries/{z}/{x}/{y}
```

---

## API Reference

### Graph & Tracks

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/graph` | All Neo4j nodes + edges (excludes Observations; limit 1 000) |
| `GET` | `/api/geotime/features` | Static features (Bases, LaunchPoints) and asset track history |
| `GET` | `/api/targets` | High-priority target list (ordered by priority, name) |
| `PUT` | `/api/targets/{id}/status` | Update target status (`Active`, `Investigated`, …) |
| `GET` | `/api/constellation` | Satellite constellation nodes |
| `POST` | `/api/chat` | Ava cognitive engine — `{"message": "..."}` → `{"reply": "..."}` |

### Imagery & Detections

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/imagery` | Satellite passes — filters: `bbox`, `start_time`, `end_time`, `sensor_type` |
| `GET` | `/api/imagery/{id}/tiles` | TiTiler tile URL template for a pass |
| `POST` | `/api/ingest` | Trigger the imagery ingest pipeline |
| `GET` | `/api/detections` | Detections — filters: `bbox`, `start_time`, `end_time`, `det_class`, `limit` |
| `GET` | `/api/detections/geojson` | Detections as GeoJSON `FeatureCollection` |
| `POST` | `/api/detections/resolve` | Entity resolution — links or creates a Target from a detection |

---

## AI Inference Service

- **Model**: prefers OBB checkpoint `inference/models/geoint_yolov8_obb.pt`; falls back to bundled `inference/yolov8n.pt`
- **Modes**: YOLOv8 OBB for trained GEOINT models; SAHI sliced prediction remains available for horizontal fallback models
- **Input**: `multipart/form-data` with `image` (PNG/JPEG, RGB) + `metadata` (JSON string)
- **Output**: `{"status": "success", "detections": [{class, bbox, confidence}], "processing_time_ms": ...}`
- **Health**: `GET /health` returns model path, model availability, SAHI availability, and device

### GPU Portability

Inference and training use `DEVICE=auto` by default: CUDA is preferred when the installed PyTorch build can use the host driver, otherwise inference falls back to CPU and training stops unless `--device cpu` is explicit. PyTorch CUDA wheels are not universal across all NVIDIA drivers, so choose the wheel index that matches the target machine when building or preparing an environment.

```bash
# Full stack with GPU inference. Use both files; docker-compose.gpu.yml is the GPU overlay.
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build

# Inference service only, with GPU access.
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 docker compose -f docker-compose.gpu.yml up -d --build

# Build only, driver reports CUDA 12.4, for example NVIDIA driver 550.x
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 docker compose -f docker-compose.yml -f docker-compose.gpu.yml build inference

# Newer CUDA wheel families can be selected the same way.
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 docker compose -f docker-compose.yml -f docker-compose.gpu.yml build inference

# CPU-only full stack for machines without NVIDIA GPUs.
TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu docker compose up -d --build
```

Do not run `docker compose -f docker-compose.gpu.yml up -d` when you want the full application stack; that file starts only the inference service. Use `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d` for all services plus GPU inference.

For direct `.venv` usage, install the matching PyTorch wheel before training or running `uvicorn`:

```bash
pip uninstall -y torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

On multi-GPU machines, training auto-selects all visible GPUs by passing `device=0,1,...` to Ultralytics. Override with `--device 0`, `--device 1`, or `--device 0,1` to limit training to specific GPUs. Dataloader workers and CPU compute threads are calculated automatically unless `--workers`, `OMP_NUM_THREADS`, or `MKL_NUM_THREADS` are set.

Inference also auto-selects all visible GPUs. The service loads one model replica per GPU and round-robins concurrent requests across those replicas. To restrict inference devices, set `DEVICE=0`, `DEVICE=0,1`, `DEVICE=cuda:2`, or `DEVICE=cpu`. The `/detect` endpoint offloads model work to a threadpool, while each model replica is locked so concurrent requests do not share the same model object unsafely. On CPU-only machines, start inference with `python inference/serve.py`; it automatically calculates Uvicorn worker processes and CPU threads per process. Override with `WEB_CONCURRENCY` or `CPU_THREADS` only when needed.

### GEOINT Model Training

Large public GEOINT datasets have different access models. xView and DOTA commonly require manual download/terms acceptance; RarePlanes can be synced from public S3; FAIR1M can be pulled from available mirrors such as Hugging Face when permitted; DIOR-R, SODA-A, and HRSC2016 are pulled from IEEE DataPort, Kaggle, or the official project pages and placed manually under `training_dataset/raw/<name>/`. fMoW is intentionally excluded — its labels are scene/site categories (airport, hospital, …) rather than object boxes and therefore do not produce useful OBB targets.

```bash
# Prepare raw data into YOLOv8 OBB format under ./training_dataset/yolo.
# --max-instances-per-class caps any single class so xView's small_car
# does not dominate training; pick a value close to your second-largest class.
python inference/prepare_datasets.py \
    --datasets xview dota fair1m dior sodaa hrsc2016 \
    --max-instances-per-class 50000 --clean

# Import manually downloaded archives/directories.
# xView expects train_images.zip, train_labels.zip, and val_images.zip.
python inference/prepare_datasets.py --dataset-archive xview=D:\data\xview\train_images.zip --dataset-archive xview=D:\data\xview\train_labels.zip --dataset-archive xview=D:\data\xview\val_images.zip
python inference/prepare_datasets.py --dataset-archive dota=D:\data\DOTA-v1.0
python inference/prepare_datasets.py --dataset-archive dior=D:\data\dior.zip --dataset-archive sodaa=D:\data\sodaa.zip --dataset-archive hrsc2016=D:\data\HRSC2016_dataset.zip

# Best-effort public downloads for datasets that support open CLI access
python inference/prepare_datasets.py --datasets rareplanes fair1m --download

# Train and promote best.pt to inference/models/geoint_yolov8_obb.pt
python inference/train_model.py --data training_dataset/yolo/data.yaml --epochs 100 --imgsz 640 --device 0
```

---

## Development

```bash
# Frontend (hot reload)
cd frontend && npm install && npm run dev

# Backend (auto-reload)
cd backend && uvicorn main:app --reload --port 8080

# Celery worker
cd backend && celery -A worker.celery_app worker -Q imagery,default --loglevel=info

# Frontend production build (TypeScript check + Vite bundle)
cd frontend && npm run build
```

---

## Component Details

| Component | Technology | Version |
|-----------|-----------|---------|
| Graph DB | Neo4j | 5.20.0 |
| Spatial DB | PostGIS | 16-3.4 |
| GDAL | gdal-bin | 3.10.3 |
| Backend | Python / FastAPI | 3.11 |
| Tile server | TiTiler | latest |
| Vector tiles | Martin | latest |
| AI inference | YOLOv8n + SAHI | ultralytics 8.x |
| Worker queue | Celery + Redis | redis:alpine |
| Reverse proxy | Nginx | alpine |
| Frontend | React | 19 |
| Build tool | Vite | 8 |
| CSS | Tailwind CSS | v4 |
| 2D map | react-leaflet | 5 |
| 3D globe | CesiumJS + react-globe.gl | 1.124 |
| Graph viz | react-force-graph-2d | latest |
