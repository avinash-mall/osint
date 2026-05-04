# Gotham GEOINT Platform

An open-source GEOINT exploitation platform inspired by Palantir Gotham. Ingests satellite imagery, fuses detections into a graph ontology, and surfaces the picture through a dark-mode tactical dashboard.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Nginx  :8090  (tile cache proxy + FMV HLS static files)     │
├──────────────┬───────────────┬──────────────────────────────-┤
│  Frontend    │  Backend API  │  Inference                     │
│  React 19    │  FastAPI      │  YOLO + DINO + MMRotate + LSK  │
│  :3000       │  :8080        │  :8002 / :8004 / :8005 / :8006 │
├──────────────┴───────────────┴────────────────────────────────┤
│  Celery worker (imagery + default queues)                     │
├──────────┬───────────────┬──────────┬──────────┬─────────────┤
│  Neo4j   │  PostGIS      │  Redis   │  TiTiler │  Martin     │
│  :7474   │  :5432        │  :6379   │  :8081   │  :3001      │
│  :7687   │               │          │  (COG)   │  (MVT)      │
└──────────┴───────────────┴──────────┴──────────┴─────────────┘
```

**13 services** — all containerised, including YOLO OBB, Grounding DINO, MMRotate, and LSKNet inference services.

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
| AI inference | YOLOv8 OBB + Grounding DINO + MMRotate (Oriented R-CNN) + LSKNet |
| Frontend | React 19 · TypeScript · Vite 8 · Tailwind CSS v4 |
| Map | react-leaflet (2D) · react-globe.gl (3D globe) · CesiumJS 1.124 (3D terrain) |
| GPU | NVIDIA RTX 50-series (Blackwell `sm_120`) supported via CUDA 12.8 |
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

Default Compose runs Grounding DINO on CPU through the `inference-lae-dino` service. Use the GPU overlay only on machines with the NVIDIA container runtime (supports RTX 30/40/50-series):

```bash
# CPU full stack
docker compose up -d --build

# GPU full stack
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

> **LLM (Ava):** point `.env` → `OPENAI_API_BASE` at a local vLLM / Ollama instance.
> Without it the Ava chat tab returns a graceful error — all other tabs work offline.

### Optical-Defense Detection Workflow

The detector is now configured as an optical-only, OBB-first analyst-review workflow. Source labels are normalized into defense parent classes, known distractors such as dams and sports courts are disabled by default, and low-confidence detections are surfaced as review candidates rather than confirmed targets.

Detailed operating instructions and next steps are in [ProjectPlan/OPTICAL_DEFENSE_DETECTION.md](ProjectPlan/OPTICAL_DEFENSE_DETECTION.md).

| Setting | Default | Purpose |
|---|---|---|
| `DETECTION_THRESHOLD_PROFILE` | `recall_review` | Recall-first review mode |
| `CONFIDENCE_THRESHOLD` | `0.12` for YOLO, `0.25` for Grounding DINO | Global inference floor |
| `MMROTATE_CONFIDENCE_THRESHOLD` | `0.10` | MMRotate service floor before taxonomy thresholds |
| `INFERENCE_CHIP_SIZE` | `1024` | Better small-object recall than 640 px chips |
| `INFERENCE_CHIP_OVERLAP` | `256` | Reduces chip-boundary misses |
| `MAX_INFERENCE_CHIPS` | `0` | Full raster coverage; no silent chip sampling |

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
| `INFERENCE_LAE_DINO_URL` | `http://inference-lae-dino:8001` | Internal Grounding DINO open-vocabulary inference service |
| `INFERENCE_MMROTATE_URL` | `http://inference-mmrotate:8001` | Internal MMRotate rotated-object inference service |
| `INFERENCE_LSKNET_URL` | `http://inference-lsknet:8001` | Internal LSKNet large selective kernel inference service |
| `IMAGERY_PATH` | `/data/imagery` | Shared volume mount |
| `OPENAI_API_BASE` | *(unset)* | Local LLM endpoint |
| `OPENAI_API_KEY` | `dummy` | |
| `OPENAI_MODEL` | `google/gemma-4-31B-it` | |
| `DETECTION_THRESHOLD_PROFILE` | `recall_review` | Detection policy profile: `recall_review`, `balanced`, or `high_precision` |
| `GLOBAL_CONFIDENCE_FLOOR` | profile default | Optional inference confidence floor override |
| `HIGH_CONFIDENCE_THRESHOLD` | profile default | Confidence required for `high_confidence` review status |
| `ENABLED_PARENT_CLASSES` | defense parent classes | Comma-separated enabled parent classes |
| `DISABLED_PARENT_CLASSES` | `dam,recreation,water,unknown` | Comma-separated distractor classes suppressed by policy |
| `INFERENCE_CHIP_CONCURRENCY` | `16` | Chip dispatch concurrency to inference providers |
| `INFERENCE_CHIP_TIMEOUT_S` | `120` | Timeout for inference requests |
| `PER_CLASS_CONFIDENCE_OVERRIDES` | `{}` | JSON map of parent/original class thresholds |

---

## Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `neo4j` | `neo4j:5.20.0` | 7474 / 7687 | Graph ontology + APOC |
| `postgis` | `postgis/postgis:16-3.4` | 5432 | Spatial catalog, detections |
| `backend` | `sentinelos-backend:latest` | 8080 | REST API |
| `worker` | `sentinelos-backend:latest` | — | Celery imagery worker |
| `frontend` | `sentinelos-frontend:latest` | 3000 | Vite dev server |
| `titiler` | `developmentseed/titiler:latest` | 8081 | COG tile server |
| `martin` | `maplibre/martin:latest` | 3001 | PostGIS → MVT |
| `inference` | `sentinelos-inference:latest` | 8002 -> 8001 | YOLOv8 OBB optical detection |
| `inference-lae-dino` | `sentinelos-inference-lae-dino:cpu` or `:gpu` | 8004 -> 8001 | Grounding DINO open-vocabulary detection |
| `inference-mmrotate` | `sentinelos-inference-mmrotate:cpu` or `:gpu` | 8005 -> 8001 | MMRotate DOTA Oriented R-CNN rotated detection |
| `inference-lsknet` | `sentinelos-inference-lsknet:cpu` or `:gpu` | 8006 -> 8001 | LSKNet DOTA rotated-object detection |
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

Current optical-defense inference uses overlapping 1024x1024 RGB chips by default, OBB-aware cross-chip dedupe, and full-raster coverage unless `MAX_INFERENCE_CHIPS` is explicitly capped. Stored detections include parent class, original class, calibrated confidence, review status, threshold profile, provider confirmation, chip provenance, model/taxonomy version, and coverage metadata. When multiple providers are selected, detections are confirmed only when more than one provider overlaps the same object (cross-provider consensus). Detections without cross-provider agreement are discarded to reduce false positives in high-precision workflows.

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
4. Slices the COG into overlapping 1024x1024 uint8 PNG chips by default
5. Sends each chip to the selected inference provider (`POST /detect`)
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

- **Model**: prefers TensorRT engine `inference/models/geoint_yolov8_obb.engine` on GPU when present; falls back to OBB checkpoint `inference/models/geoint_yolov8_obb.pt`, then bundled `inference/yolov8n.pt`
- **Modes**: YOLOv8 OBB for trained GEOINT models; SAHI sliced prediction remains available for horizontal fallback models
- **YOLO acceleration**: GPU builds enable TensorRT dependencies, automatic per-GPU TensorRT export, and YOLO micro-batching; default runtime is `YOLO_RUNTIME=auto`, `YOLO_TRT_AUTO_EXPORT=1`, `YOLO_BATCH_MAX_SIZE=8`, `YOLO_BATCH_TIMEOUT_MS=10`
- **Open vocabulary**: Grounding DINO is available at `http://localhost:8004` through the `inference-lae-dino` service and defaults to the official LAE-80C vocabulary in period-separated chunks
- **MMRotate**: DOTA v1.0 Oriented R-CNN is available at `http://localhost:8005` through the `inference-mmrotate` service and is selectable from imagery upload.
- **LSKNet**: Large Selective Kernel Network for DOTA is available at `http://localhost:8006` through the `inference-lsknet` service.
- **Stability**: Sequential PTX JIT warmups are performed on startup for MMRotate and LSKNet to prevent API timeouts during initial kernel compilation on new GPUs.
- **Policy**: optical-defense taxonomy enriches detections with parent classes and review metadata; official LAE-80C vocabulary detections are not hard-suppressed by the distractor policy.
- **Input**: `multipart/form-data` with `image` (PNG/JPEG, RGB) + `metadata` (JSON string)
- **Output**: `{"status": "success", "detections": [{class, bbox, confidence}], "processing_time_ms": ...}`
- **Health**: `GET /health` returns active runtime, engine path, engine availability, batcher stats, warmup result, torch/CUDA info, model availability, SAHI availability, and device

Current detection responses also include `original_class`, `parent_class`, `calibrated_confidence`, `review_status`, `policy_review_status`, `threshold_profile`, `model_version`, `taxonomy_version`, provider confirmation metadata, `prompt_profile`, and prompt chunk metadata. `GET /health` includes the active detection policy, provider model/config details, Grounding DINO model ID, processor status, Transformers version, and LAE prompt profile.

### Grounding DINO CPU/GPU Images

The base Compose file builds Grounding DINO with `inference-lae-dino/Dockerfile.cpu` and image tag `sentinelos-inference-lae-dino:cpu`. The GPU overlay switches only that service to `inference-lae-dino/Dockerfile.gpu`, image tag `sentinelos-inference-lae-dino:gpu`, `gpus: all`, and `DEVICE=auto`.

```bash
# CPU build and health check
docker compose build inference-lae-dino
docker compose up -d inference-lae-dino
curl http://localhost:8004/health

# GPU build and health check
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build inference-lae-dino
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d inference-lae-dino
curl http://localhost:8004/health
```

The verified CPU image uses:

```text
torch 2.4.0+cpu
numpy
transformers >=4.42,<5
Grounding DINO model snapshot at /opt/grounding-dino
```

The GPU image uses CUDA 12.8 with `torch 2.7.1+cu128`, which supports newer NVIDIA cards such as RTX 50-series / `sm_120`. The GPU overlay runs Grounding DINO with `LAE_BATCH_MAX_SIZE=1` by default because 1024 px satellite chips can OOM on 16 GB cards when multiple chips are batched. Mixed-precision is managed via `LAE_AUTOCAST_DTYPE=auto`, which probes `bf16` -> `fp16` -> `fp32` and selects the first stable format for the hardware. Startup health is verified via a silicon-level sanity probe (`LAE_MIN_PROBE_DETECTIONS`) before marking the service as ready.

Healthy Grounding DINO startup should report `model_loaded: true`, `processor_loaded: true`, a non-empty `model_id`, a non-empty `transformers_version`, and `device: cpu` for the CPU path.

### GPU Portability

Inference and training use `DEVICE=auto` by default: CUDA is preferred when the installed PyTorch build can use the host driver, otherwise inference falls back to CPU and training stops unless `--device cpu` is explicit. PyTorch CUDA wheels are not universal across all NVIDIA drivers, so choose the wheel index that matches the target machine when building or preparing an environment.

```bash
# Full stack with GPU YOLO and GPU Grounding DINO. Use both files; docker-compose.gpu.yml is the GPU overlay.
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build

# Inference services only, with GPU access. This does not start the databases, backend, or frontend.
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 docker compose -f docker-compose.gpu.yml up -d --build

# Build only, driver reports CUDA 12.4, for example NVIDIA driver 550.x
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 docker compose -f docker-compose.yml -f docker-compose.gpu.yml build inference

# Newer CUDA wheel families can be selected the same way.
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 docker compose -f docker-compose.yml -f docker-compose.gpu.yml build inference

# CPU-only full stack for machines without NVIDIA GPUs. Grounding DINO uses Dockerfile.cpu by default.
TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu docker compose up -d --build
```

Do not run `docker compose -f docker-compose.gpu.yml up -d` when you want the full application stack; that file starts only the GPU inference overrides. Use `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d` for all services plus GPU inference.

For direct `.venv` usage, install the matching PyTorch wheel before training or running `uvicorn`:

```bash
pip uninstall -y torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

On multi-GPU machines, training auto-selects all visible GPUs by passing `device=0,1,...` to Ultralytics. Override with `--device 0`, `--device 1`, or `--device 0,1` to limit training to specific GPUs. Dataloader workers and CPU compute threads are calculated automatically unless `--workers`, `OMP_NUM_THREADS`, or `MKL_NUM_THREADS` are set.

YOLO inference auto-selects all visible GPUs when the GPU overlay is used. The service loads one model replica per GPU and round-robins concurrent requests across those replicas. To restrict YOLO inference devices, set `DEVICE=0`, `DEVICE=0,1`, `DEVICE=cuda:2`, or `DEVICE=cpu`. Grounding DINO uses `DEVICE=cpu` in the base Compose file and `DEVICE=auto` with `gpus: all` in the GPU overlay. The `/detect` endpoints offload model work to a threadpool, while each model replica is locked so concurrent requests do not share the same model object unsafely. On CPU-only machines, start inference with `python inference/serve.py`; it automatically calculates Uvicorn worker processes and CPU threads per process. Override with `WEB_CONCURRENCY` or `CPU_THREADS` only when needed.

YOLO TensorRT engines are specific to the GPU, driver, CUDA, and TensorRT runtime. The GPU service handles this automatically by default: on startup, if `YOLO_RUNTIME=auto` and `YOLO_TRT_AUTO_EXPORT=1`, it checks the engine metadata, exports an FP16 engine for the current GPU when needed, and tries `YOLO_TRT_EXPORT_BATCHES=8,4,2,1` until one fits GPU memory. It then clamps runtime batching to the exported engine's max batch.

Manual export is still available for pre-warming or troubleshooting:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build inference
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d inference
docker compose -f docker-compose.yml -f docker-compose.gpu.yml exec inference \
    python export_yolo_tensorrt.py \
    --model /app/models/geoint_yolov8_obb.pt \
    --engine /app/models/geoint_yolov8_obb.engine \
    --imgsz 1024 --batch 8 --precision fp16
docker compose -f docker-compose.yml -f docker-compose.gpu.yml restart inference
curl http://localhost:8002/health
```

The export utility also writes `<engine>.json` metadata. The service reads that sidecar on startup and clamps `YOLO_BATCH_MAX_SIZE` to the engine's exported maximum batch, so a memory-constrained engine such as batch 4 will not be overfed even if Compose requests batch 8. Set `YOLO_TRT_FORCE_REEXPORT=1` to force a fresh engine build on the next startup.

Use INT8 only after preparing representative calibration data:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml exec inference \
    python export_yolo_tensorrt.py \
    --precision int8 --data /training_dataset/yolo/data.yaml
```

### GEOINT Model Training

The training pipeline ingests six public OBB-annotated aerial datasets and produces a single class-balanced YOLOv8-OBB training set. fMoW is intentionally excluded — its labels are scene/site categories (airport, hospital, …) rather than object boxes and do not produce useful OBB targets.

| Dataset      | Categories                                            | Annotation format            | Source                                                                                                |
|--------------|-------------------------------------------------------|------------------------------|-------------------------------------------------------------------------------------------------------|
| **xView**    | 60 GEOINT (vehicles, aircraft, ships, infrastructure) | GeoJSON + GeoTIFFs           | [xviewdataset.org](https://xviewdataset.org/) (registration required)                                 |
| **DOTA-v2**  | 18 (vehicles, aircraft, ships, sport facilities, …)   | DatasetNinja JSON or labelTxt | [captain-whu DOTA](https://captain-whu.github.io/DOTA/dataset.html)                                   |
| **FAIR1M**   | 37 fine-grained (planes, ships, vehicles, courts, …)  | PASCAL VOC XML               | [Hugging Face mirror](https://huggingface.co/datasets/blanchon/FAIR1M) or the official challenge page |
| **DIOR-R**   | 20 (aircraft, airports, vehicles, ships, structures)  | YOLO-OBB TXT or VOC XML      | [Kaggle YOLOv11-OBB mirror](https://www.kaggle.com/datasets/redzapdos123/dior-r-dataset-yolov11-obb-format) or [IEEE DataPort](https://ieee-dataport.org/documents/dior) |
| **SODA-A**   | 9 (airplane, helicopter, ships, vehicles, …)          | COCO JSON with `poly` field  | [shaunyuan22.github.io/SODA](https://shaunyuan22.github.io/SODA/)                                     |
| **HRSC2016** | 28 fine-grained ship classes                          | HRSC XML                     | [IEEE DataPort](https://ieee-dataport.org/documents/hrsc2016-0) or community mirrors                  |

RarePlanes (synthetic + real planes, COCO JSON) is also supported via `process_coco`; pull from the public `s3://rareplanes-public` bucket if you want to add it.

#### Raw data layout

Place each dataset under `training_dataset/raw/<name>/`. Drop in either the raw archive (`*.zip`, `*.tar`, `*.tgz` — the prep script auto-extracts) or the already-extracted directory tree:

```
training_dataset/raw/
├── xview/      # train_images.tgz, train_labels.tgz, val_images.tgz
├── dota/       # Dota.tar  (or extracted train/, val/, test-dev/ with img/ and ann/)
├── fair1m/     # data/images/*.tif + data/labelXmls/*.xml
├── dior/       # dior.zip  (or extracted train/, val/, test/ each with images/ and labels/)
├── sodaa/      # sodaa.zip (or extracted Annotations/{train,val,test}/*.json + Images/*.jpg)
└── hrsc2016/   # HRSC2016_dataset.zip (or AllImages/*.bmp + Annotations/*.xml)
```

#### Prepare

The default preparation mode is now `--taxonomy optical-defense`. It collapses source-specific labels into defense parent classes, preserves original labels in `manifest.jsonl`, and keeps distractor-only tiles as hard negatives unless `--include-distractors` is set.

```bash
# Verify each parser independently first — catches malformed/missing archives early.
# Each run prints a balance report and per-dataset tile/label counts.
for ds in xview dota fair1m dior sodaa hrsc2016; do
    python inference/prepare_datasets.py --datasets "$ds" --max-instances-per-class 50000
done

# Combined class-balanced run. --max-instances-per-class caps any single class
# (set this to roughly the size of your largest meaningful class). Without it
# xView's small_car alone produces 200k+ instances and dominates training,
# collapsing minority classes to zero.
python inference/prepare_datasets.py \
    --datasets xview dota fair1m dior sodaa hrsc2016 \
    --tile-size 1024 \
    --overlap 0.2 \
    --include-empty-ratio 0.05 \
    --hard-negative-ratio 0.5 \
    --max-instances-per-class 50000 \
    --clean
```

A successful combined run writes the YOLO dataset and audit artifacts under `training_dataset/yolo/`: `data.yaml`, `classes.json`, `taxonomy.json`, `manifest.jsonl`, `split_summary.json`, `class_distribution.csv`, `source_distribution.csv`, and `object_size_distribution.csv`. Inspect these before training. If any single-dataset run prints `0 tiles, 0 labels`, the diagnostics block tells you what the parser missed.

Importing pre-staged archives without rearranging the raw tree:

```bash
python inference/prepare_datasets.py \
    --dataset-archive xview=/path/to/train_images.tgz \
    --dataset-archive xview=/path/to/train_labels.tgz \
    --dataset-archive xview=/path/to/val_images.tgz \
    --dataset-archive dota=/path/to/Dota.tar \
    --dataset-archive dior=/path/to/dior.zip \
    --dataset-archive sodaa=/path/to/sodaa.zip \
    --dataset-archive hrsc2016=/path/to/HRSC2016_dataset.zip \
    --max-instances-per-class 50000 --clean
```

For datasets with open CLI access (RarePlanes via S3, FAIR1M via Hugging Face):

```bash
python inference/prepare_datasets.py --datasets rareplanes fair1m --download
```

#### Train

The trainer promotes `best.pt` from the run to `inference/models/geoint_yolov8_obb.pt` and writes a metadata sidecar so the inference container picks it up on the next restart.

```bash
# Single GPU
python inference/train_model.py \
    --data training_dataset/yolo/data.yaml \
    --base-model yolov8s-obb.pt \
    --epochs 100 --imgsz 1024 --batch auto --device 0

# Multi GPU (e.g. 4× H100 / A100)
python inference/train_model.py \
    --data training_dataset/yolo/data.yaml \
    --base-model yolov8s-obb.pt \
    --epochs 100 --imgsz 1024 --batch 64 --device 0,1,2,3
```

With ~120 classes and ~1.6M labels, `yolov8n-obb.pt` (the default base) is undersized — use `yolov8s-obb.pt` (small, ~11M params) or `yolov8m-obb.pt` (medium, ~26M params) for meaningful per-class accuracy. Larger backbones cost proportionally more wall-clock per epoch but are necessary at this class count.

---

### Current Promotion Policy

Training promotion is blocked unless final validation recall is at least `0.525`, the current copied-run baseline. Use `--promote-anyway` only after reviewing class-wise metrics and the failure benchmark. With the defense taxonomy, `yolov8s-obb.pt` or `yolov8m-obb.pt` should be the first baselines.

Audit a copied run without the original dataset:

```bash
python inference/audit_training_run.py --run-dir training_dataset/runs/geoint_yolov8
```

Repair copied YOLO metadata when `taxonomy.json` is missing:

```bash
python inference/repair_yolo_artifacts.py --yolo-root training_dataset/yolo
```

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
| AI inference | YOLOv8 OBB + Grounding DINO | ultralytics 8.x, Transformers |
| Worker queue | Celery + Redis | redis:alpine |
| Reverse proxy | Nginx | alpine |
| Frontend | React | 19 |
| Build tool | Vite | 8 |
| CSS | Tailwind CSS | v4 |
| 2D map | react-leaflet | 5 |
| 3D globe | CesiumJS + react-globe.gl | 1.124 |
| Graph viz | react-force-graph-2d | latest |
