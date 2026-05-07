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

**14 services** — all containerised, including YOLO OBB, Grounding DINO, MMRotate, LSKNet, SAM2, and SAM3 inference services.

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
| AI inference | YOLOv8 OBB · Grounding DINO · MMRotate (Oriented R-CNN) · LSKNet · SAM 2.1 · SAM 3 / 3.1 (open-vocab text + image-exemplar) · DINOv3 ViT-L (SAT-493M satellite + LVD-1689M general) embedder · Prithvi-EO-2.0 (flood / burn-scar / multi-temporal-crop heads) · TerraMind v1 large (S1↔S2 any-to-any generative EO) |
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

### Open-Vocabulary Detection Workflow

The detector is configured as an OBB-first, **open-vocabulary** analyst-review workflow. Every label a model emits — whether from YOLO, Grounding DINO / LAE-DINO, MMRotate, LSKNet, SAM2 (class-agnostic masks), or SAM3 (text-prompted segmentation) — is accepted as a first-class object class. There is no closed taxonomy, no per-class threshold, and no distractor suppression: detections are kept unless the operator explicitly raises `GLOBAL_CONFIDENCE_FLOOR` or `PER_CLASS_CONFIDENCE_OVERRIDES`.

| Setting | Default | Purpose |
|---|---|---|
| `DETECTION_THRESHOLD_PROFILE` | `open` | Informational profile name stored on each detection |
| `GLOBAL_CONFIDENCE_FLOOR` | `0.0` | Single floor applied to every class. `0.0` means "accept everything" |
| `HIGH_CONFIDENCE_THRESHOLD` | `0.5` | Tag threshold for `high_confidence` review status |
| `PER_CLASS_CONFIDENCE_OVERRIDES` | `{}` | Optional JSON map of class-specific floors |
| `CONFIDENCE_THRESHOLD` | `0.08` for YOLO, `0.10` for Grounding DINO | Per-service inference floor at the model level |
| `NMS_IOU_THRESHOLD` | `0.70` | YOLO OBB NMS — higher preserves tightly packed objects |
| `GROUNDING_DINO_BOX_THRESHOLD` / `_TEXT_THRESHOLD` | `0.15` | Open-vocabulary box / text floor |
| `MMROTATE_CONFIDENCE_THRESHOLD` | `0.05` | MMRotate service floor |
| `LSKNET_CONFIDENCE_THRESHOLD` | `0.05` | LSKNet service floor |
| `MAX_DETECTIONS_PER_CHIP` | `1000` | Per-chip detection cap |
| `INFERENCE_CHIP_SIZE` | `1024` | Better small-object recall than 640 px chips |
| `INFERENCE_CHIP_OVERLAP` | `512` | 50 % overlap so objects on chip boundaries appear fully in ≥1 chip |
| `MAX_INFERENCE_CHIPS` | `0` | Full raster coverage; no silent chip sampling |

`parent_class_for_label` clusters detections into broad open buckets (aircraft, vessel, vehicle, train, building, infrastructure, storage_tank, bridge, harbor, airfield, recreation, vegetation, water, person, animal, food, furniture, household, electronic, tool, clothing, plant, sport, segment, track) and falls back to the **normalized label itself** when no cluster matches — true open vocabulary. The `segment` parent catches SAM2 auto-mode masks; the `track` parent catches SAM3 video tracks. Cross-provider consensus is still applied in multi-provider runs.

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
| `INFERENCE_SAM3_URL` | `http://inference-sam3:8001` | Internal SAM3 open-vocabulary segmentation/tracking service |
| `IMAGERY_PATH` | `/data/imagery` | Shared volume mount |
| `OPENAI_API_BASE` | *(unset)* | Local LLM endpoint |
| `OPENAI_API_KEY` | `dummy` | |
| `OPENAI_MODEL` | `google/gemma-4-31B-it` | |
| `DETECTION_THRESHOLD_PROFILE` | `open` | Informational profile label stored with each detection |
| `GLOBAL_CONFIDENCE_FLOOR` | `0.0` | Single confidence floor; 0.0 means "accept everything" |
| `HIGH_CONFIDENCE_THRESHOLD` | `0.5` | Threshold at which a detection is tagged `high_confidence` |
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

Each inference service is gated by a docker-compose `profile` (`yolo`, `lae-dino`, `mmrotate`, `lsknet`, `sam2`, `sam3`, plus the meta-profile `all`). Default `docker compose up` brings up zero inference containers — the `backend` and `worker` services own their lifecycle:

1. **One-time provisioning** — `COMPOSE_PROFILES=all docker compose create` builds & registers all inference containers in stopped state.
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
| `inference-sam3` | `sentinelos-inference-sam3:gpu` | internal 8001 | Meta SAM 3 / 3.1 — open-vocabulary `/detect` (RGB · multispectral · SAR-via-RGB-proxy) and `/detect_video` (Object Multiplex FMV tracking). Returns mask RLE + normalized HBB + minAreaRect OBB + DINOv3 embedding; optional Prithvi flood/burn/crop overlays and TerraMind SAR features behind loader flags. See [SAM 3 — Open-Vocabulary RGB / Multispectral / SAR / FMV](#sam-3--open-vocabulary-rgb--multispectral--sar--fmv) below. |
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

Open-vocabulary inference uses overlapping 1024x1024 RGB chips by default, OBB-aware cross-chip dedupe, and full-raster coverage unless `MAX_INFERENCE_CHIPS` is explicitly capped. Stored detections include parent class, original (open-vocab) class, calibrated confidence, review status, threshold profile, provider confirmation, chip provenance, model/taxonomy version, and coverage metadata. When multiple providers are selected, detections are confirmed only when more than one provider overlaps the same object (cross-provider consensus). Detections without cross-provider agreement are discarded to reduce false positives.

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
- **SAM 3**: `inference-sam3` adds text-prompted open-vocabulary segmentation for RGB chips, 6+ band multispectral GeoTIFF chips, VV/VH SAR chips via a TerraMind RGB proxy, and `/detect_video` for SAM3.1 Object Multiplex FMV tracking. It returns normalized HBBs, mask-derived normalized OBBs, COCO RLE masks, `original_class`, coarse `parent_class`, and optional DINOv3/Prithvi/TerraMind metadata.
- **Stability**: Sequential PTX JIT warmups are performed on startup for MMRotate and LSKNet to prevent API timeouts during initial kernel compilation on new GPUs.
- **Policy**: open-vocabulary — every label a model emits is preserved as `original_class`, with a coarse `parent_class` cluster for UI grouping. No per-class threshold and no distractor suppression by default.
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

### Grounded SAM Family (class-tagged segmentation)

SAM 2 has no classification head, and SAM3 can also refine boxes into class-tagged masks. To make their output useful as part of the detection pipeline, the worker drives grounded SAM providers with the boxes produced by the *other* selected providers on the same chip. SAM then returns one tight mask per input box, tagged with that box's class.

**`/detect` modes** (selected automatically by the worker — no config needed):

| Trigger | Mode | Returns |
|---|---|---|
| `metadata.prompt_boxes` is a non-empty list | **Grounded** — single `SAM2ImagePredictor.set_image()` + batched `predict(box=...)` | One detection per input box: `class` / `original_class` / `parent_class` inherited from the prompt, `obb` traced from the predicted mask via `cv2.minAreaRect`, `confidence = max(source_confidence, mask_iou)`, plus `mask_iou`, `source_provider`, `source_confidence`, `area`, `task: "grounded_segmentation"` |
| `prompt_boxes` is missing or empty | **Auto** — `SAM2AutomaticMaskGenerator.generate()` | Class-agnostic masks tagged `class: "segment"` (handled by the `segment` parent class in the policy) |

**Two-phase chip dispatch in [backend/worker.py](backend/worker.py)** — when the upload selects SAM 2 alongside any non-grounded detector:

1. Phase 1: dispatch the chip to every non-grounded provider in the selection (any combination of `yolo`, `lae-dino`, `mmrotate`, `lsknet`, plus future detectors).
2. Phase 2: union their detections into a `prompt_boxes` payload (capped at 256 boxes/chip, sorted by source confidence) and post the chip to SAM 2 with that metadata.
3. Fallbacks: SAM 2 selected alone → unprompted auto mode. SAM 2 selected with others but no phase-1 boxes on a chip → unprompted auto mode for that chip. Phase-1 provider failures → logged and skipped, surviving boxes still drive SAM 2.

**Adding another grounded-by-prompt provider in the future** is one line: add the provider name to `GROUNDED_PROVIDERS` in [backend/worker.py](backend/worker.py). `sam3` is already included alongside `sam2`; the two-phase dispatch and the consensus-exempt safety net pick it up automatically.

**Cross-provider consensus** (`apply_confirmation_policy`): grounded SAM detections inherit the source detector's `parent_class`, so they cross-confirm naturally with the originating box in `deduplicate_detections` → `confirmation_status: "confirmed"`, `confirmation_reason: "cross_provider"`. The `CONSENSUS_EXEMPT_PROVIDERS = {"sam2", "sam3"}` set keeps auto-mode/open-vocabulary SAM outputs from being dropped when they run without corroborating detectors.

### SAM 3 — Open-Vocabulary RGB / Multispectral / SAR / FMV

`inference-sam3` is a single FastAPI service that bundles five pretrained models — **no training, no fine-tuning, weights-only**:

| Component | Model ID | Size (FP16) | Role |
|---|---|---|---|
| **SAM 3 image** | `facebook/sam3.1` (or `facebook/sam3`) | ~1.5 GB | Promptable concept segmentation — text + image-exemplar prompts, returns `{masks, boxes, scores}` for every matching instance ([HF docs](https://huggingface.co/docs/transformers/main/en/model_doc/sam3)) |
| **SAM 3.1 video** | `build_sam3_multiplex_video_predictor()` | ~1.5 GB | Object Multiplex multi-object tracker — joint propagation in shared memory, ~7× faster than per-object tracking at 128 objects on H100 ([release notes](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md)) |
| **DINOv3-SAT-L** | `facebook/dinov3-vitl16-pretrain-sat493m` | ~600 MB | Frozen embedder — 1024-d CLS tokens trained on 493 M Maxar 0.6 m chips ([HF card](https://huggingface.co/facebook/dinov3-vitl16-pretrain-sat493m)) |
| **DINOv3-LVD-L** *(opt-in)* | `facebook/dinov3-vitl16-pretrain-lvd1689m` | ~600 MB | Frozen embedder for FMV / oblique imagery ([HF card](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m)) |
| **Prithvi-EO-2.0 heads** *(opt-in)* | `Prithvi-EO-2.0-300M-TL-Sen1Floods11` · `Prithvi-EO-2.0-300M-BurnScars` · `Prithvi-EO-1.0-100M-multi-temporal-crop-classification` | ~3 GB total | Multispectral overlays — flood / water (3-class), burn scar (binary), 13 CDL crop classes (3-timestep) |
| **TerraMind-1.0-large** *(opt-in)* | `ibm-esa-geospatial/TerraMind-1.0-large` | ~6 GB | SAR backbone (S1GRD VV/VH 2-band) + S1→S2L2A any-to-any generation for the SAM3 RGB proxy |

#### Endpoints

| Method | Path | Use |
|---|---|---|
| `GET`  | `/health` | Lazy-load status; lists every loaded model and its model id |
| `POST` | `/detect` | Per-chip image segmentation — RGB, multispectral, or SAR (multipart `image` + JSON `metadata`) |
| `POST` | `/detect_video` | FMV tracking — multipart `video` (or `metadata.video_path`); streams `application/x-ndjson`, one record per frame×track |

#### Per-modality contract (image)

The worker auto-selects modality from the raster; callers can override via `metadata.modality`.

| Modality | `metadata.modality` | Chip format | Pipeline |
|---|---|---|---|
| **Optical RGB satellite / aerial** | `rgb` *(default)* | uint8 PNG (1024×1024 from the worker's `chip_to_uint8_rgb`) | SAM3 text or box prompts → mask + bbox + OBB + DINOv3-SAT embedding |
| **Multispectral (HLS-6 / S2-L2A)** | `multispectral` | float32 6-band GeoTIFF — Blue, Green, Red, Narrow-NIR, SWIR-1, SWIR-2 (Prithvi `constant_scale=0.0001`) | Resize to 224 → Prithvi flood + burn → SAM3 on the RGB preview → optional 3-timestep crop classifier when `metadata.hls_timesteps == 3` |
| **SAR (Sentinel-1 GRD)** | `sar` | float32 2-band GeoTIFF (VV, VH; dB clipped to [-30, 0] then linear-stretched to [0, 1]) | TerraMind S1→S2L2A → bands 3,2,1 RGB preview → SAM3 prompts on the synthetic preview, `confidence` capped at `SAM3_SAR_CONF_CAP=0.85`, `sar_proxy: true` and `review_status: review_candidate` always set |
| **FMV (video)** | sent via `/detect_video` | MP4 / MOV / TS / AVI / MPEG-TS | SAM 3.1 Object Multiplex session — `start_session → add_prompt(text) → handle_stream_request(propagate_in_video) → close_session`. One DINOv3-LVD embedding per track on its first frame. |

#### Output schema (per detection)

```json
{
  "class": "building",
  "original_class": "a building",
  "parent_class": "building",
  "bbox": [cx_norm, cy_norm, w_norm, h_norm],
  "obb": [x1, y1, ..., x4, y4],          // 8-elem normalized xyxyxyxy
  "obb_format": "yolo_obb_normalized_xyxyxyxy",
  "obb_source": "mask_min_area_rect",     // or "hbb_fallback"
  "obb_angle_deg": -59.5,
  "obb_area_px": 1861.5,
  "edge_truncated": false,
  "confidence": 0.887,
  "mask_rle": {"size":[H,W],"counts":"<base64 COCO RLE>"},
  "area": 1938,                            // mask area in pixels
  "modality": "rgb",
  "task": "sam3_open_vocab_object_detection",
  "embedding": {
    "model": "facebook/dinov3-vitl16-pretrain-sat493m",
    "dim": 1024,
    "fp16_b64": "<base64 fp16 vector>"
  },
  "prithvi_labels": ["water", "crop:corn"],     // multispectral path only
  "sar_proxy": false,                            // true on SAR (synthetic RGB)
  "terramind_embedding": null                    // 768-d on SAR when TerraMind loaded
}
```

The video endpoint streams one JSON object per frame×track with the same shape plus `frame_index` and `track_id`.

#### Open vocabulary — every text phrase is a label

The platform is open-vocab by construction: SAM 3 was trained on **~4 M unique noun-phrase concepts** from the SA-Co dataset, so the prompt *is* the label. Defaults are auto-selected per modality:

| Profile | Auto-applied to | Source vocabularies | Count |
|---|---|---|---|
| `satellite_v1` | `rgb` · `multispectral` · `sar` | xView · DOTA v2.0 · DIOR · fMoW · FAIR1M · HRSC2016 ship-types · RarePlanes attributes (deduped) | **214 prompts** ([prompts/satellite_v1.json](inference-sam3/prompts/satellite_v1.json)) |
| `ground_v1` | `fmv` | COCO 2017 · Objects365 v2 + LVIS v1 curated extension | **576 prompts** ([prompts/ground_v1.json](inference-sam3/prompts/ground_v1.json)) |

Override priority (each step skips the rest):

1. `metadata.text_prompts: ["..."]` — arbitrary list.
2. `metadata.prompt_profile: "satellite_v1"|"ground_v1"|"<custom>"` — pick a shipped profile or a custom `<custom>.json` next to it.
3. `SAM3_LABEL_FILE=/app/prompts/custom.json` — env-pinned override.
4. **Auto-select** by `metadata.modality` (FMV → `ground_v1`, everything else → `satellite_v1`).

All prompts pass through trim → lowercase → dedupe-preserve-order → cap at `SAM3_MAX_PROMPTS_PER_REQUEST` (default 1024). Empty resolved list → HTTP 400. To regenerate the JSONs from the source taxonomies, run `python prompts/_build_satellite_v1.py` or `python prompts/_build_ground_v1.py` inside `inference-sam3/`.

#### Backend integration — already wired

| Hook | Behavior |
|---|---|
| `INFERENCE_PROVIDERS["sam3"]` in [backend/worker.py:46](backend/worker.py#L46) | Provider key registered with the URL `INFERENCE_SAM3_URL` |
| `GROUNDED_PROVIDERS = {"sam2", "sam3"}` in [backend/worker.py:732](backend/worker.py#L732) | SAM3 also receives `prompt_boxes` from phase-1 detectors when run alongside YOLO/LAE-DINO/MMRotate/LSKNet |
| `CONSENSUS_EXEMPT_PROVIDERS = {"sam2", "sam3"}` in [backend/worker.py:498](backend/worker.py#L498) | Open-vocab masks survive single-provider runs |
| `_emit_chip_payload` in [backend/worker.py:896](backend/worker.py#L896) | Emits 2-band SAR / 6-band MSI GeoTIFFs for SAM3-only chips, RGB PNG otherwise |
| `process_fmv` Celery task in [backend/worker.py:1450](backend/worker.py#L1450) | Streams NDJSON detections from `/detect_video` into the `fmv_detections` table |
| `_KNOWN_INFERENCE_PROVIDERS` in [backend/main.py:3294](backend/main.py#L3294) | `sam3` accepted in `inference_providers` form fields |
| `PROVIDER_TO_SERVICE["sam3"]` in [backend/provider_lifecycle.py:30](backend/provider_lifecycle.py#L30) | Lifecycle manager starts/stops the container on demand |

#### Bringing it up

```bash
# 1. Detect host + populate SAM3_* build args (CUDA / Torch / TorchVision / arch list).
python scripts/configure_host.py            # writes the SENTINELOS GENERATED GPU CONFIG block

# 2. Make sure HF_TOKEN is in .env with approved gating for facebook/sam3.1 +
#    facebook/dinov3-vitl16-pretrain-{sat493m,lvd1689m}.
grep -E "^HF_TOKEN=" .env

# 3. Build the image (~5–10 min depending on bandwidth + Torch wheel cache).
COMPOSE_PROFILES=sam3 docker compose build inference-sam3

# 4. Materialize all stopped containers once so the lifecycle manager can start them.
COMPOSE_PROFILES=all docker compose create

# 5. Start the service. First /detect downloads the gated weights into the
#    sam3_models named volume (writes to /models/hf/hub).
COMPOSE_PROFILES=sam3 docker compose up -d inference-sam3
docker exec osint-inference-sam3-1 curl -sS http://127.0.0.1:8001/health | jq .

# 6. Probe an RGB chip end-to-end.
docker cp inference-sam3/probes/probe_chip.png osint-inference-sam3-1:/tmp/
docker exec osint-inference-sam3-1 \
  curl -s -F image=@/tmp/probe_chip.png \
       -F 'metadata={"text_prompts":["a building","a road"],"modality":"rgb"}' \
       http://127.0.0.1:8001/detect | jq '.detections | length'
```

Once weights are in the volume you can flip the runtime to fully offline:

```env
SAM3_HF_HUB_OFFLINE=1
SAM3_TRANSFORMERS_OFFLINE=1
```

#### VRAM budget — per-component loader flags

The image always loads SAM 3 image + SAM 3.1 video. Auxiliaries are env-flagged so a 16 GB GPU can run a useful subset:

| Flag | Default in compose | Adds (≈ FP16) | Enables |
|---|---|---|---|
| `SAM3_LOAD_DINOV3_SAT` | `1` | ~0.6 GB | `embedding` field on satellite/aerial detections |
| `SAM3_LOAD_DINOV3_LVD` | `0` | ~0.6 GB | `embedding` field on FMV tracks |
| `SAM3_LOAD_PRITHVI` | `0` | ~3 GB | `prithvi_labels: ["water","burn_scar","crop:<class>"]` on multispectral chips |
| `SAM3_LOAD_TERRAMIND` | `0` | ~6 GB | SAR S1→S2 generation + `terramind_embedding` (else SAM3 falls back to a deterministic SAR-as-RGB stretch) |
| `SAM3_LOAD_OPTIONAL_MODELS` | `0` | — | Master switch — when `0`, the four flags above default off; set to `1` to flip them all on at once |

Approximate steady-state VRAM observed on the smoke run (RTX 5070 Ti, 16 GB): SAM 3 + SAM 3.1 video + DINOv3-SAT-L = **~11 GB used**. Loading Prithvi + TerraMind on top pushes close to 22 GB — use a 24 GB+ GPU for the full configuration.

#### `inference-sam3` service env (compose)

| Variable | Default | Purpose |
|---|---|---|
| `SAM3_DEVICE` | `auto` | Reuses the inference-sam2 device-resolution logic; set to `cuda:0` / `cpu` to override |
| `SAM3_IMAGE_MODEL_ID` | `facebook/sam3.1` | Image checkpoint; `facebook/sam3` also valid |
| `SAM3_USE_MULTIPLEX` | `1` | `1` = SAM 3.1 `build_sam3_multiplex_video_predictor`, `0` = plain SAM 3 |
| `SAM3_BACKEND` | `auto` | `auto` tries `transformers.Sam3Model` then falls back to the native repo API; force with `transformers` or `native` |
| `SAM3_TEXT_THRESHOLD` | `0.30` | Minimum SAM3 score for text-prompt detections |
| `SAM3_BOX_THRESHOLD` | `0.25` | Minimum SAM3 score for box-prompt detections |
| `SAM3_PRITHVI_OVERLAY_THRESHOLD` | `0.30` | Mask × Prithvi-overlay IoU at which the overlay label is appended |
| `SAM3_SAR_CONF_CAP` | `0.85` | Hard cap on confidence for SAR detections (synthetic RGB proxy) |
| `SAM3_OBB_OPENING_KERNEL_PCT` | `0.01` | Morphological opening kernel as a fraction of the smaller mask extent before `cv2.minAreaRect` |
| `SAM3_OBB_MIN_AREA_PX` | `4` | Minimum contour area before falling back to HBB |
| `SAM3_MAX_PROMPTS_PER_REQUEST` | `1024` | Cap on resolved prompts after dedupe |
| `SAM3_DEFAULT_PROMPT_PROFILE` | *(empty → modality auto)* | Force a profile (`satellite_v1` / `ground_v1` / custom) regardless of modality |
| `SAM3_LABEL_FILE` | *(unset)* | Optional path to a JSON file with a `prompts` array — overrides the modality-auto path |
| `SAM3_HF_HUB_OFFLINE` / `SAM3_TRANSFORMERS_OFFLINE` | `0` | Flip to `1` once the `sam3_models` volume is populated |
| `HF_TOKEN` | from host `.env` | Required at first run to fetch gated `facebook/sam3*` and `facebook/dinov3-vitl16-pretrain-*` checkpoints |

Build-time args (`SAM3_CUDA_VERSION`, `SAM3_TORCH_INDEX_URL`, `SAM3_TORCH_VERSION`, `SAM3_TORCHVISION_VERSION`, `SAM3_TORCH_CUDA_ARCH_LIST`, `SAM3_GPU_PROFILE`) are written by `scripts/configure_host.py`.

#### Sample `/detect` invocations

```bash
# A. Open-vocab RGB satellite chip (default modality=rgb)
curl -F image=@chip.png \
     -F 'metadata={"text_prompts":["airplane","ship","oil tanker","helipad"]}' \
     http://inference-sam3:8001/detect | jq '.detections[] | {original_class, confidence}'

# B. Multispectral 6-band HLS GeoTIFF — adds Prithvi flood + burn overlays
#    (Prithvi loader flag must be 1)
curl -F image=@hls6.tif \
     -F 'metadata={"modality":"multispectral"}' \
     http://inference-sam3:8001/detect | jq '.detections[].prithvi_labels'

# C. SAR (Sentinel-1 GRD VV/VH) — TerraMind generates the optical proxy
#    (TerraMind loader flag must be 1; otherwise the deterministic SAR-RGB
#    stretch is used and detections are still labelled `sar_proxy: true`)
curl -F image=@s1grd.tif \
     -F 'metadata={"modality":"sar","text_prompts":["a ship"]}' \
     http://inference-sam3:8001/detect | jq '.detections[] | {original_class, sar_proxy, confidence}'

# D. FMV — streaming NDJSON, one record per frame × track
curl -F video=@clip.mp4 \
     -F 'metadata={"text_prompts":["a person","a car"],"frame_stride":2}' \
     http://inference-sam3:8001/detect_video > tracks.ndjson
wc -l tracks.ndjson
```

#### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build fails with `error: externally-managed-environment` (PEP 668) | Ubuntu 24.04 base | Already fixed — Dockerfile sets `PIP_BREAK_SYSTEM_PACKAGES=1` (matches the other inference services) |
| `/detect` 503 with `cannot import name 'Sam3Model' from 'transformers'` | `transformers` ≤ 4.57 doesn't ship SAM3 yet | The runner falls back to the native `sam3` repo API automatically. Force with `SAM3_BACKEND=native`; flip back to `transformers` after upgrading to a release that includes SAM3 |
| `RuntimeError: mat1 and mat2 must have the same dtype, but got BFloat16 and Float` | Native model is fp32 but autocast was previously off | Already fixed — native path now wraps inference in `torch.autocast(device_type="cuda", dtype=torch.bfloat16)` |
| `HF 401/403` during first `/detect` | `HF_TOKEN` missing or no approved gating | Apply for access at the model card pages; ensure `HF_TOKEN` is in `.env` and that compose passes it through (it does by default) |
| `OutOfMemoryError` at startup | Loaded too many auxiliaries for the GPU | Set `SAM3_LOAD_PRITHVI=0` / `SAM3_LOAD_TERRAMIND=0` / `SAM3_LOAD_DINOV3_LVD=0`; restart the container |
| `400 No labels supplied for SAM3` | Prompt resolver couldn't find anything | Check `metadata.text_prompts` is a non-empty list, or that the auto-select profile JSON exists at `inference-sam3/prompts/<name>.json` |

#### Licenses

| Component | License | Gating |
|---|---|---|
| SAM 3 / SAM 3.1 code + weights | [Meta SAM License](https://github.com/facebookresearch/sam3/blob/main/LICENSE) — read before commercial use | **Gated** |
| DINOv3 weights | [Meta DINOv3 License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/) | **Gated** |
| Prithvi-EO-2.0 weights | Apache 2.0 | Open |
| TerraMind v1 weights | Apache 2.0 | Open |

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

The default preparation mode is `--taxonomy optical-defense` (legacy training script). It collapses source-specific labels into broad parent classes and preserves the original labels in `manifest.jsonl`. The runtime detection policy is open-vocabulary regardless of which taxonomy a checkpoint was trained against — the original label is what gets stored.

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

Training promotion is blocked unless final validation recall is at least `0.525`, the current copied-run baseline. Use `--promote-anyway` only after reviewing class-wise metrics and the failure benchmark. `yolov8s-obb.pt` or `yolov8m-obb.pt` are reasonable first baselines.

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
