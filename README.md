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
# 1. Detect this host's GPU/driver and write build settings to .env
python scripts/configure_host.py

# 2. Build all images (use --profile all so inference Dockerfiles are built too)
COMPOSE_PROFILES=all docker compose build

# 3. Materialize all inference containers in a stopped state (one-time).
#    Inference services are profile-gated and start on demand per upload —
#    see "Dynamic Inference Lifecycle" below.
COMPOSE_PROFILES=all docker compose create

# 4. Start the platform (no inference containers come up by default)
docker compose up -d

# 5. Wait for databases to be healthy (~30 s), then seed
docker exec -it osint-backend-1 python seed.py         # Neo4j ontology
docker exec -it osint-backend-1 python seed_postgis.py # PostGIS passes + detections
docker exec -it osint-backend-1 python add_targets.py  # HPTL targets
docker exec -it osint-backend-1 python add_constellation.py  # Satellite constellation

# 6. Open the dashboard
open http://localhost:3000
```

> **Legacy / dev mode** — to keep all inference services running 24/7 (pre-lifecycle behavior), bring them up with `COMPOSE_PROFILES=all docker compose up -d` or set `PROVIDER_LIFECYCLE_ENABLED=false` in `.env`.

The host preflight reads `nvidia-smi`, resolves the matching CUDA/PyTorch profile, and updates only the generated GPU block in `.env`. Run it once per host, and rerun it after changing GPUs or NVIDIA drivers.

```bash
python scripts/configure_host.py
docker compose up -d --build
```

> **LLM (Ava):** point `.env` → `OPENAI_API_BASE` at a local vLLM / Ollama instance.
> Without it the Ava chat tab returns a graceful error — all other tabs work offline.

### Optical-Defense Detection Workflow

The detector is now configured as an optical-only, OBB-first analyst-review workflow. Source labels are normalized into defense parent classes, known distractors such as dams and sports courts are disabled by default, and low-confidence detections are surfaced as review candidates rather than confirmed targets.

Detailed operating instructions and next steps are in [ProjectPlan/OPTICAL_DEFENSE_DETECTION.md](ProjectPlan/OPTICAL_DEFENSE_DETECTION.md).

| Setting | Default | Purpose |
|---|---|---|
| `DETECTION_THRESHOLD_PROFILE` | `recall_review` | Recall-first review mode |
| `CONFIDENCE_THRESHOLD` | `0.08` for YOLO, `0.10` for Grounding DINO | Service inference floor (policy gate is applied downstream) |
| `NMS_IOU_THRESHOLD` | `0.70` | YOLO OBB NMS — higher value preserves tightly-packed objects (e.g. parking lots) |
| `GROUNDING_DINO_BOX_THRESHOLD` / `_TEXT_THRESHOLD` | `0.15` | Open-vocabulary box / text confidence floor |
| `MMROTATE_CONFIDENCE_THRESHOLD` | `0.05` | MMRotate service floor before taxonomy thresholds |
| `LSKNET_CONFIDENCE_THRESHOLD` | `0.05` | LSKNet service floor before taxonomy thresholds |
| `MAX_DETECTIONS_PER_CHIP` | `1000` | Per-chip detection cap (raised from 300 for dense scenes) |
| `INFERENCE_CHIP_SIZE` | `1024` | Better small-object recall than 640 px chips |
| `INFERENCE_CHIP_OVERLAP` | `512` | 50 % overlap so objects spanning chip boundaries appear fully in ≥1 chip |
| `MAX_INFERENCE_CHIPS` | `0` | Full raster coverage; no silent chip sampling |

The active policy enables 11 parent classes (`aircraft`, `ship`, `vehicle`, `military_vehicle`, `storage_tank`, `bridge`, `harbor`, `airfield`, `building`, `infrastructure`, `segment`) and disables `dam`, `recreation`, `water`, `unknown`. The `segment` class catches SAM2 mask outputs (auto-mode fallback) — when SAM2 runs in grounded mode, masks inherit the source detector's class and bypass the `segment` parent entirely.

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://neo4j:7687` | Graph database |
| `NEO4J_USERNAME` | `neo4j` | |
| `NEO4J_PASSWORD` | `password` | |
| `POSTGIS_URI` | `postgresql://gotham:gotham@postgis:5432/gotham` | Spatial database |
| `POSTGIS_POOL_MIN` | `1` | Minimum PostGIS connections held per backend/worker process |
| `POSTGIS_POOL_MAX` | `10` | Maximum PostGIS connections held per backend/worker process |
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
| `INFERENCE_MAX_PENDING_CHIPS` | `32` | Maximum encoded raster chips queued while inference requests run |
| `INFERENCE_CHIP_SPOOL_MAX_BYTES` | `4194304` | Encoded chip PNGs larger than this spill to a temp file |
| `INFERENCE_CHIP_TIMEOUT_S` | `120` | Timeout for inference requests |
| `PER_CLASS_CONFIDENCE_OVERRIDES` | `{}` | JSON map of parent/original class thresholds |
| `PROVIDER_LIFECYCLE_ENABLED` | `true` | Toggle dynamic start/stop of inference containers per upload |
| `PROVIDER_START_TIMEOUT_S` | `120` | Per-provider healthcheck deadline when starting |
| `PROVIDER_HEALTH_POLL_INTERVAL_S` | `2` | `/health` poll cadence while waiting for a provider |
| `PROVIDER_IDLE_COOLDOWN_S` | `600` | Idle window before a provider container is auto-stopped |
| `PROVIDER_IDLE_CHECK_INTERVAL_S` | `60` | Cadence of the celery-beat `stop_idle_providers` sweep |

---

## Dynamic Inference Lifecycle

Each inference service is gated by a docker-compose `profile` (`yolo`, `lae-dino`, `mmrotate`, `lsknet`, `sam2`, plus the meta-profile `all`). Default `docker compose up` brings up zero inference containers — the `backend` and `worker` services own their lifecycle:

1. **One-time provisioning** — `COMPOSE_PROFILES=all docker compose create` builds & registers all five inference containers in stopped state.
2. **On upload** — `POST /api/ingest/upload` reads `inference_providers=...` and calls `provider_lifecycle.ensure_running(...)` ([backend/provider_lifecycle.py](backend/provider_lifecycle.py)) to start the requested containers via the Docker Engine API and wait for `/health` (≤ `PROVIDER_START_TIMEOUT_S`). Failures bubble up as HTTP 503.
3. **During processing** — the celery worker calls `mark_active(...)` to record a Redis last-used timestamp.
4. **Idle reaping** — celery-beat (`--beat` is now part of the worker command) runs `stop_idle_providers` every `PROVIDER_IDLE_CHECK_INTERVAL_S`. Any provider whose last-used timestamp is older than `PROVIDER_IDLE_COOLDOWN_S` is `docker stop`-ed.

The `backend` and `worker` services mount `/var/run/docker.sock` so they can manage sibling containers. To disable the dynamic behavior (e.g. for local dev or CI) set `PROVIDER_LIFECYCLE_ENABLED=false` and bring containers up manually with `COMPOSE_PROFILES=all docker compose up -d`.

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
| `inference-sam2` | `sentinelos-inference-sam2:gpu` | 8007 -> 8001 | Meta SAM 2.1 Hiera; auto-mask or grounded-by-prompt segmentation |
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
- **SAM 2**: Meta SAM 2.1 Hiera (tiny / small / base+ / large) is available at `http://localhost:8007` through the `inference-sam2` service. The `/detect` endpoint operates in two modes selected automatically by the worker — see [Grounded SAM 2 (class-tagged segmentation)](#grounded-sam-2-class-tagged-segmentation) below.
- **Stability**: Sequential PTX JIT warmups are performed on startup for MMRotate and LSKNet to prevent API timeouts during initial kernel compilation on new GPUs.
- **Policy**: optical-defense taxonomy enriches detections with parent classes and review metadata; official LAE-80C vocabulary detections are not hard-suppressed by the distractor policy.
- **Input**: `multipart/form-data` with `image` (PNG/JPEG, RGB) + `metadata` (JSON string)
- **Output**: `{"status": "success", "detections": [{class, bbox, confidence}], "processing_time_ms": ...}`
- **Health**: `GET /health` returns active runtime, engine path, engine availability, batcher stats, warmup result, torch/CUDA info, model availability, SAHI availability, and device

> **Note** — `/detect` is a *single-chip worker*. The production code path is `POST /api/ingest/upload`, where the celery worker (`backend/worker.py:slice_and_infer`) tiles the raster into 1024 × 1024 chips with 50 % overlap and fans them out to the providers selected on upload. Calling `/detect` directly with a full multi-thousand-pixel raster will under-detect because the model downsamples internally. Use the orchestrator path for benchmarking, or enable Internal Tiling (below) for ad-hoc QA.

### Internal Tiling for direct `/detect`

Each detection service (YOLO, LAE Grounding DINO, MMRotate, LSKNet) supports an opt-in tile-and-merge fallback so direct `/detect` calls on full rasters return dense detections without the orchestrator. Default is **off** to preserve the chip-worker contract used by the celery worker.

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_INTERNAL_TILING` | `off` | `off` \| `on` (always tile) \| `auto` (tile when `max(W,H) > 2 × tile`) |
| `INFERENCE_TILE_SIZE` | `1024` | Tile dimension; YOLO uses `YOLO_IMGSZ` |
| `INFERENCE_TILE_OVERLAP` | `512` | 50 % overlap so objects spanning boundaries appear fully in ≥1 tile |
| `INFERENCE_TILE_NMS_IOU` | `0.5` | Cross-tile per-class greedy NMS IoU on axis-aligned bounding rects of the OBBs |

When tiling fires the response sets `"internal_tiled": true` and includes `"inference_diagnostics": {tiles, raw_detections, after_cross_tile_nms, tile_size, tile_overlap}`.

Current detection responses also include `original_class`, `parent_class`, `calibrated_confidence`, `review_status`, `policy_review_status`, `threshold_profile`, `model_version`, `taxonomy_version`, provider confirmation metadata, `prompt_profile`, and prompt chunk metadata. `GET /health` includes the active detection policy, provider model/config details, Grounding DINO model ID, processor status, Transformers version, and LAE prompt profile.

### Grounded SAM 2 (class-tagged segmentation)

SAM 2 has no classification head — it produces class-agnostic masks. To make its output useful as part of the detection pipeline, the worker drives SAM 2 with the boxes produced by the *other* selected providers on the same chip ("Grounded SAM" pattern, [facebookresearch/sam2 docs](https://github.com/facebookresearch/sam2)). SAM 2 then returns one tight mask per input box, tagged with that box's class.

**`/detect` modes** (selected automatically by the worker — no config needed):

| Trigger | Mode | Returns |
|---|---|---|
| `metadata.prompt_boxes` is a non-empty list | **Grounded** — single `SAM2ImagePredictor.set_image()` + batched `predict(box=...)` | One detection per input box: `class` / `original_class` / `parent_class` inherited from the prompt, `obb` traced from the predicted mask via `cv2.minAreaRect`, `confidence = max(source_confidence, mask_iou)`, plus `mask_iou`, `source_provider`, `source_confidence`, `area`, `task: "grounded_segmentation"` |
| `prompt_boxes` is missing or empty | **Auto** — `SAM2AutomaticMaskGenerator.generate()` | Class-agnostic masks tagged `class: "segment"` (handled by the `segment` parent class in the policy) |

**Two-phase chip dispatch in [backend/worker.py](backend/worker.py)** — when the upload selects SAM 2 alongside any non-grounded detector:

1. Phase 1: dispatch the chip to every non-grounded provider in the selection (any combination of `yolo`, `lae-dino`, `mmrotate`, `lsknet`, plus future detectors).
2. Phase 2: union their detections into a `prompt_boxes` payload (capped at 256 boxes/chip, sorted by source confidence) and post the chip to SAM 2 with that metadata.
3. Fallbacks: SAM 2 selected alone → unprompted auto mode. SAM 2 selected with others but no phase-1 boxes on a chip → unprompted auto mode for that chip. Phase-1 provider failures → logged and skipped, surviving boxes still drive SAM 2.

**Adding another grounded-by-prompt provider in the future** is one line: add the provider name to `GROUNDED_PROVIDERS` in [backend/worker.py](backend/worker.py). The two-phase dispatch and the consensus-exempt safety net pick it up automatically — no other code changes.

**Cross-provider consensus** (`apply_confirmation_policy`): grounded SAM 2 detections inherit the source detector's `parent_class`, so they cross-confirm naturally with the originating box in `deduplicate_detections` → `confirmation_status: "confirmed"`, `confirmation_reason: "cross_provider"`. The `CONSENSUS_EXEMPT_PROVIDERS = {"sam2"}` set keeps auto-mode `segment` outputs from being dropped on chips where SAM 2 ran without prompts.

### Grounding DINO GPU Image

The Compose file builds Grounding DINO with `inference-lae-dino/Dockerfile.gpu`, image tag `sentinelos-inference-lae-dino:gpu`, `gpus: all`, and `DEVICE=auto`. Run host preflight before building so the image uses the right CUDA/PyTorch stack.

```bash
python scripts/configure_host.py
docker compose build inference-lae-dino
docker compose up -d inference-lae-dino
curl http://localhost:3000/inference/lae/health
```

The verified CPU image uses:

```text
torch 2.4.0+cpu
numpy
transformers >=4.42,<5
Grounding DINO model snapshot at /opt/grounding-dino
```

The GPU image uses the CUDA/PyTorch versions generated by `python scripts/configure_host.py` for the current host. For example, A100 hosts use the Ampere CUDA 12.4 / PyTorch 2.6 profile, while RTX 50-series hosts use the Blackwell CUDA 12.8 / PyTorch 2.7 profile when the driver is compatible. Grounding DINO runs with `LAE_BATCH_MAX_SIZE=1` by default because 1024 px satellite chips can OOM on smaller cards when multiple chips are batched. Mixed-precision is managed via `LAE_AUTOCAST_DTYPE=auto`, which probes `bf16` -> `fp16` -> `fp32` and selects the first stable format for the hardware. Startup health is verified via a silicon-level sanity probe (`LAE_MIN_PROBE_DETECTIONS`) before marking the service as ready.

Healthy Grounding DINO startup should report `model_loaded: true`, `processor_loaded: true`, a non-empty `model_id`, a non-empty `transformers_version`, and `device: cpu` for the CPU path.

### GPU Portability

Inference services use `DEVICE=auto` by default, but Docker image build args must match the host GPU and NVIDIA driver. Do not hand-edit CUDA/PyTorch build settings or copy them between machines. Run the preflight instead:

```bash
python scripts/configure_host.py
docker compose up -d --build
```

The preflight fails before build when a profile requires a newer host driver. For example, A100 hosts resolve to the Ampere CUDA 12.4 / PyTorch 2.6 profile, while RTX 50-series hosts resolve to the Blackwell CUDA 12.8 / PyTorch 2.7 profile only when the driver is new enough.

For direct `.venv` usage, install the matching PyTorch wheel before training or running `uvicorn`:

```bash
pip uninstall -y torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

On multi-GPU machines, training auto-selects all visible GPUs by passing `device=0,1,...` to Ultralytics. Override with `--device 0`, `--device 1`, or `--device 0,1` to limit training to specific GPUs. Dataloader workers and CPU compute threads are calculated automatically unless `--workers`, `OMP_NUM_THREADS`, or `MKL_NUM_THREADS` are set.

YOLO inference auto-selects all visible GPUs. The service loads one model replica per GPU and round-robins concurrent requests across those replicas. To restrict YOLO inference devices, set `DEVICE=0`, `DEVICE=0,1`, `DEVICE=cuda:2`, or `DEVICE=cpu`. Grounding DINO also uses `DEVICE=auto` with `gpus: all`. The `/detect` endpoints offload model work to a threadpool, while each model replica is locked so concurrent requests do not share the same model object unsafely. On CPU-only machines, start inference with `python inference/serve.py`; it automatically calculates Uvicorn worker processes and CPU threads per process. Override with `WEB_CONCURRENCY` or `CPU_THREADS` only when needed.

YOLO TensorRT engines are specific to the GPU, driver, CUDA, and TensorRT runtime. The GPU service handles this automatically by default: on startup, if `YOLO_RUNTIME=auto` and `YOLO_TRT_AUTO_EXPORT=1`, it checks the engine metadata, exports an FP16 engine for the current GPU when needed, and tries `YOLO_TRT_EXPORT_BATCHES=8,4,2,1` until one fits GPU memory. It then clamps runtime batching to the exported engine's max batch.

Manual export is still available for pre-warming or troubleshooting:

```bash
python scripts/configure_host.py
docker compose build inference
docker compose up -d inference
docker compose exec inference \
    python export_yolo_tensorrt.py \
    --model /app/models/geoint_yolov8_obb.pt \
    --engine /app/models/geoint_yolov8_obb.engine \
    --imgsz 1024 --batch 8 --precision fp16
docker compose restart inference
curl http://localhost:3000/inference/main/health
```

The export utility also writes `<engine>.json` metadata. The service reads that sidecar on startup and clamps `YOLO_BATCH_MAX_SIZE` to the engine's exported maximum batch, so a memory-constrained engine such as batch 4 will not be overfed even if Compose requests batch 8. Set `YOLO_TRT_FORCE_REEXPORT=1` to force a fresh engine build on the next startup.

Use INT8 only after preparing representative calibration data:

```bash
docker compose exec inference \
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
