# Sentinel

An open-source GEOINT exploitation platform that ingests satellite imagery, fuses detections into a graph ontology, and surfaces the picture through a dark-mode tactical dashboard. Inference is consolidated on **SAM 3 / SAM 3.1** — open-vocabulary segmentation for RGB satellite, multispectral, and SAR imagery, plus Object Multiplex tracking on FMV.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Nginx  :3000  (reverse proxy + tile cache + FMV HLS files)  │
├──────────────┬───────────────┬──────────────────────────────-┤
│  Frontend    │  Backend API  │  Inference                     │
│  React 19    │  FastAPI      │  SAM 3 / 3.1 (segmentation,    │
│  :3000       │  :8080        │  tracking) — single service    │
├──────────────┴───────────────┴────────────────────────────────┤
│  Celery worker (imagery + default queues, beat)               │
├──────────┬───────────────┬──────────┬──────────┬─────────────┤
│  Neo4j   │  PostGIS      │  Redis   │  TiTiler │  Martin     │
│  :7474   │  :5432        │  :6379   │  (COG)   │  (MVT)      │
│  :7687   │               │          │          │             │
└──────────┴───────────────┴──────────┴──────────┴─────────────┘
```

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
| AI inference | SAM 3 / 3.1 (open-vocab segmentation + Object Multiplex video tracking) · DINOv3 ViT-L SAT-493M (re-ID embedding) · Prithvi-EO-2.0 (flood / burn-scar / multi-temporal-crop heads) · TerraMind v1 large (S1↔S2 generative EO) · DOTA-OBB (aerial vehicle/plane detector) · Grounding DINO (auto-gated open-vocab fallback) |
| Frontend | React 19 · TypeScript · Vite 8 · Tailwind CSS v4 |
| Map | react-leaflet (2D) · react-globe.gl (3D globe) · CesiumJS 1.124 (3D terrain) |
| GPU | NVIDIA RTX 50-series (Blackwell `sm_120`) supported via CUDA 12.8 |
| Reverse proxy | Nginx alpine — tile cache (24 h TTL) + FMV HLS serving |

---

## Quick Start

```bash
# 1. Detect host GPU/driver and write build settings to .env
python scripts/configure_host.py

# 2. Build the SAM3 inference image
docker compose build inference-sam3

# 3. Start the platform, including SAM3 inference
docker compose up -d

# 4. Open the dashboard
open http://localhost:3000
```

The host preflight reads `nvidia-smi`, resolves the matching CUDA/PyTorch profile, and updates only the generated GPU block in `.env`. Run it once per host, and rerun it after changing GPUs or NVIDIA drivers.

> **LLM (Ava):** point `.env` → `OPENAI_API_BASE` at a local vLLM / Ollama instance.
> Without it the Ava chat tab returns a graceful error — all other tabs work offline.

### Open-Vocabulary Detection Workflow

Every label SAM 3 emits — text-prompted from the active prompt profile or from `metadata.text_prompts` — is accepted as a first-class object class. There is no closed taxonomy, no per-class threshold, and no distractor suppression: detections are kept unless the operator explicitly raises `GLOBAL_CONFIDENCE_FLOOR` or `PER_CLASS_CONFIDENCE_OVERRIDES`.

| Setting | Default | Purpose |
|---|---|---|
| `DETECTION_THRESHOLD_PROFILE` | `open` | Informational profile name stored on each detection |
| `GLOBAL_CONFIDENCE_FLOOR` | `0.0` | Single floor applied to every class. `0.0` means "accept everything" |
| `HIGH_CONFIDENCE_THRESHOLD` | `0.5` | Tag threshold for `high_confidence` review status |
| `PER_CLASS_CONFIDENCE_OVERRIDES` | `{}` | Optional JSON map of class-specific floors |
| `INFERENCE_CHIP_SIZE` | `1008` | Matches SAM3's intended square image resolution |
| `INFERENCE_CHIP_OVERLAP` | `252` | 25 % overlap for boundary objects while keeping SAM3-native chip geometry |
| `MAX_INFERENCE_CHIPS` | `0` | Full raster coverage; no silent chip sampling |

`parent_class_for_label` clusters detections into broad open buckets (aircraft, vessel, vehicle, train, building, infrastructure, storage_tank, bridge, harbor, airfield, recreation, vegetation, water, person, animal, food, furniture, household, electronic, tool, clothing, plant, sport, segment, track) and falls back to the **normalized label itself** when no cluster matches — true open vocabulary. The `segment` parent catches mask outputs; the `track` parent catches SAM3 video tracks.

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://neo4j:7687` | Graph database |
| `NEO4J_USERNAME` | `neo4j` | |
| `NEO4J_PASSWORD` | `password` | |
| `POSTGIS_URI` | `postgresql://sentinel:sentinel@postgis:5432/sentinel` | Spatial database |
| `POSTGIS_POOL_MIN` | `1` | Minimum PostGIS connections held per backend/worker process |
| `POSTGIS_POOL_MAX` | `10` | Maximum PostGIS connections held per backend/worker process |
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker |
| `TITILER_URL` | `http://titiler:8080` | Internal tile server |
| `INFERENCE_SAM3_URL` | `http://inference-sam3:8001` | Internal SAM3 open-vocabulary segmentation/tracking service |
| `IMAGERY_PATH` | `/data/imagery` | Shared volume mount |
| `OPENAI_API_BASE` | *(unset)* | Local LLM endpoint |
| `OPENAI_API_KEY` | `dummy` | |
| `OPENAI_MODEL` | `google/gemma-4-31b-it` | |
| `DETECTION_THRESHOLD_PROFILE` | `open` | Informational profile label stored with each detection |
| `GLOBAL_CONFIDENCE_FLOOR` | `0.0` | Single confidence floor; 0.0 means "accept everything" |
| `HIGH_CONFIDENCE_THRESHOLD` | `0.5` | Threshold at which a detection is tagged `high_confidence` |
| `INFERENCE_CHIP_CONCURRENCY` | `4` | Chip dispatch concurrency to the SAM3 service |
| `INFERENCE_MAX_PENDING_CHIPS` | `32` | Maximum encoded raster chips queued while inference requests run |
| `INFERENCE_CHIP_SPOOL_MAX_BYTES` | `4194304` | Encoded chip PNGs larger than this spill to a temp file |
| `INFERENCE_CHIP_TIMEOUT_S` | `600` | Timeout for inference requests |
| `PER_CLASS_CONFIDENCE_OVERRIDES` | `{}` | JSON map of parent/original class thresholds |
| `PROVIDER_START_TIMEOUT_S` | `120` | SAM3 `/health` wait deadline before uploads fail fast |
| `PROVIDER_HEALTH_POLL_INTERVAL_S` | `2` | `/health` poll cadence while waiting for SAM3 |

---

## Inference Service

The SAM3 inference service is a normal docker-compose service. `docker compose up -d` starts it with the rest of the platform, and the backend/worker call `provider_lifecycle.ensure_running()` only to wait for `/health` before dispatching work. Failures bubble up as HTTP 503.

To restart only inference after config changes, run `docker compose up -d --no-deps inference-sam3`.

---

## Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `neo4j` | `neo4j:5.20.0` | 7474 / 7687 | Graph ontology + APOC |
| `postgis` | `postgis/postgis:16-3.4` | 5432 | Spatial catalog, detections |
| `backend` | `sentinel-backend:latest` | 8080 | REST API |
| `worker` | `sentinel-backend:latest` | — | Celery imagery worker |
| `frontend` | `sentinel-frontend:latest` | 3000 | Vite dev server |
| `titiler` | `developmentseed/titiler:latest` | 8081 | COG tile server |
| `martin` | `maplibre/martin:latest` | 3001 | PostGIS → MVT |
| `inference-sam3` | `sentinel-inference-sam3:gpu` | internal 8001 | Meta SAM 3 / 3.1 — open-vocabulary `/detect` (RGB · multispectral · SAR-via-RGB-proxy) and `/detect_video` (Object Multiplex FMV tracking). Returns mask RLE + normalized HBB + minAreaRect OBB + DINOv3 embedding; optional Prithvi flood/burn/crop overlays and TerraMind SAR features behind loader flags. See [SAM 3 — Open-Vocabulary RGB / Multispectral / SAR / FMV](#sam-3--open-vocabulary-rgb--multispectral--sar--fmv) below. |
| `redis` | `redis:alpine` | 6379 | Task queue |
| `nginx` | `nginx:alpine` | 3000 | Reverse proxy + tile cache + FMV HLS |

---

## Frontend Modules

The dashboard is a single-page application with a sidebar of tabs.

| Tab | Component | What it shows |
|-----|-----------|---------------|
| **Graph** | Ontology Explorer | Force-directed graph of all Neo4j nodes (Targets, Assets, Observations, Satellites, Bases, LaunchPoints) and their relationships, rendered with `react-force-graph-2d` |
| **Map** | Sentinel Map | `react-leaflet` map with CARTO Dark Matter basemap, TiTiler satellite imagery overlay (opacity slider), AI detection GeoJSON overlay colour-coded by class, asset track polylines, base/launch-point markers, time slider and layer panel |
| **Targets** | Target Workbench | High-Priority Target List — status badges, inline status updates (`PUT /api/targets/{id}/status`), detection history panel, satellite pass trigger |
| **Space** | Constellation View | `react-globe.gl` 3D globe with satellite point cloud, orbital arc overlays, and per-satellite collection window panel drawn from `/api/constellation` |
| **Browser** | Data Browser | Tabular view of raw graph nodes and telemetry from `/api/graph`; sortable columns |
| **Ava** | Cognitive Engine | Natural-language chat → `GraphCypherQAChain` (LangChain) → Neo4j Cypher → answer; shows "LLM OFFLINE" when no endpoint is configured |
| **3D** | View3D (CesiumJS) | CesiumJS 1.124 globe with offline NaturalEarth II TMS basemap via `CESIUM_BASE_URL='/cesium/'`; FMV clip integration hook |
| **Admin** | Ontology Editor (`/admin/ontology`) | DB-backed editor for branches, objects, prompts, sensors, and per-object icons. No auth (single-tenant); writes go straight to PostGIS and bump the ontology version pinned on every new detection. |

---

## Imagery Pipeline

Open-vocabulary inference uses overlapping 1008×1008 chips by default, OBB-aware cross-chip dedupe, and full-raster coverage unless `MAX_INFERENCE_CHIPS` is explicitly capped. Stored detections include parent class, original (open-vocab) class, calibrated confidence, review status, threshold profile, chip provenance, model/taxonomy version, and coverage metadata.

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
4. Slices the COG into overlapping 1008×1008 chips (PNG for RGB, GeoTIFF for multispectral/SAR)
5. Sends each chip to SAM3 (`POST /detect`)
6. Georeferences bounding boxes back to Lat/Lon
7. Stores detections in PostGIS and Neo4j

### Tile URLs

```
# COG tiles (TiTiler — direct)
http://localhost:8081/cog/tiles/{z}/{x}/{y}?url=/data/imagery/processed/pass_cog.tif

# COG tiles (Nginx cache proxy — 24 h TTL)
http://localhost:3000/tiles/cog/tiles/{z}/{x}/{y}?url=/data/imagery/processed/pass_cog.tif

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
| `POST` | `/api/ingest/upload` | Upload + ingest in one call (multipart) |
| `GET` | `/api/detections` | Detections — filters: `bbox`, `start_time`, `end_time`, `det_class`, `limit` |
| `GET` | `/api/detections/geojson` | Detections as GeoJSON `FeatureCollection` |
| `POST` | `/api/detections/resolve` | Entity resolution — links or creates a Target from a detection |

---

## SAM 3 — Open-Vocabulary RGB / Multispectral / SAR / FMV

`inference-sam3` is a single FastAPI service that bundles five pretrained models — **no training, no fine-tuning, weights-only**:

| Component | Model ID | Size (FP16) | Role |
|---|---|---|---|
| **SAM 3 image** | `facebook/sam3` | ~1.5 GB | Promptable concept segmentation via the native `Sam3Processor` API (`set_image` → `set_text_prompt` / `add_geometric_prompt`). Returns `{masks, boxes, scores}` for every matching instance. Per-image state caches backbone features so per-prompt cost is encoder-free ([upstream repo](https://github.com/facebookresearch/sam3)) |
| **SAM 3.1 video** | `build_sam3_multiplex_video_predictor()` (`facebook/sam3.1`, `sam3.1_multiplex.pt`) | ~3.5 GB | Object Multiplex multi-object tracker — joint propagation in shared memory, ~7× faster than per-object tracking at 128 objects on H100. Note: `facebook/sam3.1` ships **only** the video multiplex checkpoint; image inference stays on `facebook/sam3` ([release notes](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md)) |
| **DINOv3-SAT-L** | `facebook/dinov3-vitl16-pretrain-sat493m` | ~600 MB | Frozen embedder — 1024-d CLS tokens trained on 493 M Maxar 0.6 m chips ([HF card](https://huggingface.co/facebook/dinov3-vitl16-pretrain-sat493m)) |
| **DINOv3-LVD-L** *(opt-in)* | `facebook/dinov3-vitl16-pretrain-lvd1689m` | ~600 MB | Frozen embedder for FMV / oblique imagery ([HF card](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m)) |
| **Prithvi-EO-2.0 heads** *(opt-in)* | `Prithvi-EO-2.0-300M-TL-Sen1Floods11` · `Prithvi-EO-2.0-300M-BurnScars` · `Prithvi-EO-1.0-100M-multi-temporal-crop-classification` | ~3 GB total | Multispectral overlays — flood / water (3-class), burn scar (binary), 13 CDL crop classes (3-timestep) |
| **TerraMind-1.0-large** *(opt-in)* | `ibm-esa-geospatial/TerraMind-1.0-large` | ~6 GB | SAR backbone (S1GRD VV/VH 2-band) + S1→S2L2A any-to-any generation for the SAM3 RGB proxy |

### Endpoints

| Method | Path | Use |
|---|---|---|
| `GET`  | `/health` | Lazy-load status; lists every loaded model and its model id |
| `POST` | `/detect` | Per-chip image segmentation — RGB, multispectral, or SAR (multipart `image` + JSON `metadata`) |
| `POST` | `/detect_video` | FMV tracking — multipart `video` (or `metadata.video_path`); streams `application/x-ndjson`, one record per frame×track |

### Per-modality contract (image)

The worker auto-selects modality from the raster; callers can override via `metadata.modality`.

| Modality | `metadata.modality` | Chip format | Pipeline |
|---|---|---|---|
| **Optical RGB satellite / aerial** | `rgb` *(default)* | uint8 PNG (1008×1008 from the worker's `chip_to_uint8_rgb`) | SAM3 text prompts (via `metadata.text_prompts` / profile) or box prompts (via `metadata.prompt_boxes`, normalized cxcywh `bbox` and/or 8-pt `obb`) → mask + bbox + OBB + DINOv3-SAT embedding |
| **Multispectral (HLS-6 / S2-L2A)** | `multispectral` | float32 6-band GeoTIFF — Blue, Green, Red, Narrow-NIR, SWIR-1, SWIR-2 (Prithvi `constant_scale=0.0001`) | Resize to 224 → Prithvi flood + burn → SAM3 on the RGB preview → optional 3-timestep crop classifier when `metadata.hls_timesteps == 3` |
| **SAR (Sentinel-1 GRD)** | `sar` | float32 2-band GeoTIFF (VV, VH; dB clipped to [-30, 0] then linear-stretched to [0, 1]) | TerraMind S1→S2L2A → bands 3,2,1 RGB preview → SAM3 prompts on the synthetic preview, `confidence` capped at `SAM3_SAR_CONF_CAP=0.85`, `sar_proxy: true` and `review_status: review_candidate` always set |
| **FMV (video)** | sent via `/detect_video` | MP4 / MOV / TS / AVI / MPEG-TS | SAM 3.1 Object Multiplex session — `start_session → add_prompt(text) → handle_stream_request(propagate_in_video) → close_session`. One DINOv3-LVD embedding per track on its first frame. |

### Output schema (per detection)

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

### Open vocabulary — every text phrase is a label

The platform is open-vocab by construction: SAM 3 was trained on **~4 M unique noun-phrase concepts** from the SA-Co dataset, so the prompt *is* the label.

**Ontology** — Categories, objects, prompts, sensors, and per-object icons live in PostGIS (`ontology_branches`, `ontology_objects`, `ontology_unknown_labels` tables). Edit via the **Admin** tab in the dashboard (`/admin/ontology`, no auth — single-tenant). The seed JSON `defenceOntology.json` is treated as a one-shot seed source consumed by `backend/scripts/seed_ontology.py`; the DB is the canonical store at runtime.

**Classifier** — A single Python function `backend.ontology.normalize(label, layer)` is shared by the worker, the eval-metrics comparison harness, and any script. Unknown labels gracefully fall back to an `Other` branch with a `circle_help` icon and are logged to `ontology_unknown_labels` for admin triage.

**Per-object icons** — Curated lucide-react icons live in `frontend/src/utils/iconLibrary.tsx`. Add new entries there if you create new objects in the admin UI; the regex matcher in `branchIcons.tsx` is now a last-resort fallback.

**Prompt selection** — Sensor-default prompts are served by `GET /api/ontology/default-prompts?sensor=optical|multispectral|sar|fmv`. The shipped prompt-profile JSON files (`satellite_v1_full.json`, `ground_v1_full.json`) and `prompts/loader.py` were removed; the inference service fetches its defaults from the backend on demand and caches them for 5 minutes.

Override priority (each step skips the rest):

1. `metadata.text_prompts: ["..."]` — arbitrary list.
2. `metadata.prompt_profile: "<sensor>"` — fetch the sensor-default prompts from `/api/ontology/default-prompts`.
3. **Auto-select** by `metadata.modality` (FMV → ground default, everything else → optical default).

All prompts pass through trim → lowercase → dedupe-preserve-order → cap at `SAM3_MAX_PROMPTS_PER_REQUEST` (default 128). Empty resolved list → HTTP 400. To re-seed the DB ontology from a fresh JSON snapshot, run `python backend/scripts/seed_ontology.py`.

### Backend integration

| Hook | Behavior |
|---|---|
| `INFERENCE_SAM3_URL` in [backend/worker.py](backend/worker.py) | Single inference URL read from env; `_post_chip_to_sam3` POSTs each chip directly |
| `_emit_chip_payload` in [backend/worker.py](backend/worker.py) | Emits 2-band SAR / 6-band MSI GeoTIFFs, otherwise an RGB PNG |
| `slice_and_infer` in [backend/worker.py](backend/worker.py) | Tiles the COG, dispatches chips through a thread pool, dedupes results, returns a summary |
| `process_fmv` Celery task in [backend/worker.py](backend/worker.py) | Streams NDJSON detections from `/detect_video` into the `fmv_detections` table |
| `provider_lifecycle.ensure_running()` in [backend/provider_lifecycle.py](backend/provider_lifecycle.py) | Waits for the Compose-managed SAM3 service to answer `/health` before dispatch |
| `POST /api/ingest/upload` in [backend/main.py](backend/main.py) | No `inference_providers` form field — every imagery/FMV upload routes to SAM3 implicitly |

### Bringing it up

```bash
# 1. Detect host + populate SAM3_* build args (CUDA / Torch / TorchVision / arch list).
python scripts/configure_host.py            # writes the SENTINEL GENERATED GPU CONFIG block

# 2. Make sure HF_TOKEN is in .env with approved gating for facebook/sam3* +
#    facebook/dinov3-vitl16-pretrain-{sat493m,lvd1689m}.
grep -E "^HF_TOKEN=" .env

# 3. Build the image (~5–10 min depending on bandwidth + Torch wheel cache).
docker compose build inference-sam3

# 4. Start the service. First /detect downloads the gated weights into the
#    sam3_models named volume (writes to /models/hf/hub).
docker compose up -d inference-sam3
docker compose exec -T inference-sam3 curl -sS http://127.0.0.1:8001/health | jq .

# 5. Probe an RGB chip end-to-end.
docker compose cp inference-sam3/probes/probe_chip.png inference-sam3:/tmp/
docker compose exec -T inference-sam3 \
  curl -s -F image=@/tmp/probe_chip.png \
       -F 'metadata={"text_prompts":["a building","a road"],"modality":"rgb"}' \
       http://127.0.0.1:8001/detect | jq '.detections | length'
```

Once weights are in the volume you can flip the runtime to fully offline:

```env
SAM3_HF_HUB_OFFLINE=1
SAM3_TRANSFORMERS_OFFLINE=1
```

### VRAM budget — per-component loader flags

The image always loads SAM 3 image + SAM 3.1 video. Auxiliaries are env-flagged so a 16 GB GPU can run a useful subset:

| Flag | Default in compose | Adds (≈ FP16) | Enables |
|---|---|---|---|
| `SAM3_LOAD_DINOV3_SAT` | `1` | ~0.6 GB | `embedding` field on every detection (re-ID across images and video frames). Verified Top-1 = 100 % on DOTA augmented stills, SEP = +0.22 on 1440p drone video tracking — see *Inference Layer Comparison* below. |
| `SAM3_LOAD_PRITHVI` | `0` | ~3 GB | `prithvi_labels: ["water","burn_scar","crop:<class>"]` on multispectral chips (+20 ms/chip). |
| `SAM3_LOAD_TERRAMIND` | `0` | ~6 GB | SAR S1→S2 generation + `terramind_embedding` (else SAM3 falls back to a deterministic SAR-as-RGB stretch). |
| `SAM3_LOAD_DOTA_OBB` | `1` | ~0.05 GB | DOTA-v1 oriented-bbox specialist (mAP 0.05 → 0.61 on aerial RGB). |
| `SAM3_LOAD_GROUNDING_DINO` | `1` | ~0.5 GB | Open-vocab text-to-box fallback. **Auto-gated**: skipped server-side when every prompt is in the SAM3 + DOTA common vocab (saves ~115 ms/request). |
| `SAM3_LOAD_OPTIONAL_MODELS` | `0` | — | Master switch — when `0`, the flags above default off; set to `1` to flip them all on at once. |

> **Removed in this release**
> - `SAM3_LOAD_DEFENCE_YOLO` — DEFENCE_YOLO produced 1297 false positives / 0 true positives across 26 DOTA val chips and was removed entirely.
> - `SAM3_LOAD_DINOV3_LVD` — DINOV3_LVD produced **NaN embeddings** on real drone-video crops and was 2.5× slower than DINOV3_SAT with no measured quality advantage. Removed. See [docs/video_tracking_stability.md](docs/video_tracking_stability.md).

Approximate steady-state VRAM observed on the smoke run (RTX 5070 Ti, 16 GB): SAM 3 + SAM 3.1 video + DINOv3-SAT-L = **~11 GB used**. Loading Prithvi + TerraMind on top pushes close to 22 GB — use a 24 GB+ GPU for the full configuration.

### `inference-sam3` service env (compose)

| Variable | Default | Purpose |
|---|---|---|
| `SAM3_DEVICE` | `auto` | Set to `cuda:0` / `cpu` to override auto-selection |
| `SAM3_IMAGE_MODEL_ID` | `facebook/sam3` | Image checkpoint label exposed in `/health`. The native `build_sam3_image_model()` always loads `facebook/sam3` (upstream's only image artifact); `facebook/sam3.1` ships only the multiplex video checkpoint |
| `SAM3_USE_MULTIPLEX` | `1` | `1` = SAM 3.1 `build_sam3_multiplex_video_predictor`, `0` = plain SAM 3 |
| `SAM3_TEXT_THRESHOLD` | `0.30` | Minimum SAM3 score for text-prompt detections |
| `SAM3_BOX_THRESHOLD` | `0.25` | Minimum SAM3 score for box-prompt detections |
| `SAM3_PRITHVI_OVERLAY_THRESHOLD` | `0.30` | Mask × Prithvi-overlay IoU at which the overlay label is appended |
| `SAM3_SAR_CONF_CAP` | `0.85` | Hard cap on confidence for SAR detections (synthetic RGB proxy) |
| `SAM3_OBB_OPENING_KERNEL_PCT` | `0.01` | Morphological opening kernel as a fraction of the smaller mask extent before `cv2.minAreaRect` |
| `SAM3_OBB_MIN_AREA_PX` | `4` | Minimum contour area before falling back to HBB |
| `SAM3_MAX_PROMPTS_PER_REQUEST` | `128` | Cap on resolved prompts after dedupe |
| `ONTOLOGY_BACKEND_URL` | `http://backend:8080` | Backend URL the inference service queries for sensor-default prompts (`/api/ontology/default-prompts`). Cached in-process for 5 min; SIGHUP forces refresh. |
| `SAM3_HF_HUB_OFFLINE` / `SAM3_TRANSFORMERS_OFFLINE` | `0` | Flip to `1` once the `sam3_models` volume is populated |
| `HF_TOKEN` | from host `.env` | Required at first run to fetch gated `facebook/sam3*` and `facebook/dinov3-vitl16-pretrain-*` checkpoints |

Build-time args (`SAM3_CUDA_VERSION`, `SAM3_TORCH_INDEX_URL`, `SAM3_TORCH_VERSION`, `SAM3_TORCHVISION_VERSION`, `SAM3_TORCH_CUDA_ARCH_LIST`, `SAM3_GPU_PROFILE`, `SAM3_UBUNTU_VERSION`) are written by `scripts/configure_host.py`.

### Sample `/detect` invocations

```bash
# A. Open-vocab RGB satellite chip (default modality=rgb)
curl -F image=@chip.png \
     -F 'metadata={"text_prompts":["airplane","ship","oil tanker","helipad"]}' \
     http://inference-sam3:8001/detect | jq '.detections[] | {original_class, confidence}'

# A2. Box-prompted segmentation — refine an upstream detector's ROI into a
#     tight SAM3 mask + OBB. `bbox` is normalized cxcywh in [0,1]; `obb` is
#     accepted as an 8-pt xyxyxyxy fallback. `class` is propagated to output.
curl -F image=@chip.png \
     -F 'metadata={"prompt_boxes":[{"bbox":[0.5,0.5,0.4,0.4],"class":"vessel"}]}' \
     http://inference-sam3:8001/detect | jq '.detections[] | {class, confidence}'

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

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build fails with `error: externally-managed-environment` (PEP 668) | Ubuntu 24.04 base | Already fixed — Dockerfile sets `PIP_BREAK_SYSTEM_PACKAGES=1` |
| `RuntimeError: mat1 and mat2 must have the same dtype, but got BFloat16 and Float` | Native model is fp32 but autocast was previously off | Already fixed — inference is wrapped in `torch.autocast(device_type="cuda", dtype=torch.bfloat16)` |
| `HF 401/403` during first `/detect` | `HF_TOKEN` missing or no approved gating | Apply for access at the model card pages; ensure `HF_TOKEN` is in `.env` and that compose passes it through (it does by default) |
| `OutOfMemoryError` at startup | Loaded too many auxiliaries for the GPU | Set `SAM3_LOAD_PRITHVI=0` / `SAM3_LOAD_TERRAMIND=0`; restart the container. Also set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` (now default in compose) to reduce fragmentation. |
| `400 No labels supplied for SAM3` | Prompt resolver couldn't find anything | Check `metadata.text_prompts` is a non-empty list, or that the auto-select profile JSON exists at `inference-sam3/prompts/<name>.json` |

### Licenses

| Component | License | Gating |
|---|---|---|
| SAM 3 / SAM 3.1 code + weights | [Meta SAM License](https://github.com/facebookresearch/sam3/blob/main/LICENSE) — read before commercial use | **Gated** |
| DINOv3 weights | [Meta DINOv3 License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/) | **Gated** |
| Prithvi-EO-2.0 weights | Apache 2.0 | Open |
| TerraMind v1 weights | Apache 2.0 | Open |

---

## GPU Portability

`inference-sam3` uses `DEVICE=auto` by default, but Docker image build args must match the host GPU and NVIDIA driver. Do not hand-edit CUDA/PyTorch build settings or copy them between machines. Run the preflight instead:

```bash
python scripts/configure_host.py
docker compose up -d --build
```

The preflight fails before build when a profile requires a newer host driver. For example, A100 hosts resolve to the Ampere CUDA 12.4 / PyTorch 2.6 profile, while RTX 50-series hosts resolve to the Blackwell CUDA 12.8 / PyTorch 2.7 profile only when the driver is new enough.

---

## Inference Layer Comparison

The 7-layer inference stack was systematically benchmarked on real public data
(DOTA-v1.0 val for RGB box-detection quality, Sen1Floods11 for multispectral
PRITHVI, NASA drone footage for video re-ID, synthetic 2-band SAR for TERRAMIND
latency). Reports under [docs/](docs/):

- [docs/inference_layer_comparison.md](docs/inference_layer_comparison.md) — image-stack mAP/latency tables
- [docs/embedding_stability.md](docs/embedding_stability.md) — DINOV3_SAT augmentation re-ID quality
- [docs/video_tracking_stability.md](docs/video_tracking_stability.md) — drone-video cross-frame tracking

### Headline results (RTX 5070 Ti, 16 GB)

| Layer | Verdict | Quality | Cost / chip | Evidence |
|---|---|---|---|---|
| **SAM3 (base)** | ✅ Foundation | mAP 0.05 alone on DOTA val | 590 ms | Required for masks |
| **DOTA_OBB** | ✅ **Keep** | mAP **0.05 → 0.61** (aircraft recall **0 % → 92 %**, naval **0.6 % → 21 %**) | **+50 ms** | Single biggest quality win |
| **GROUNDING_DINO** | ✅ Keep — **auto-gated** | +0.01 mAP when forced | +115 ms (skipped 100 % of the time on common-vocab prompts) | Server-side gate at [grounding_dino_gate.py](inference-sam3/grounding_dino_gate.py) |
| **PRITHVI** | ✅ Keep | Per-pixel flood/burn (chip-level metric N/A through current API) | **+20 ms** | 10/10 Sen1Floods chips ran clean post-fix |
| **DINOV3_SAT** | ✅ Keep | Top-1 re-ID **100 %** on stills, SEP **+0.22** on 1440p drone video | +217 ms / 293 ms embed | Only embedding worth keeping |
| **TERRAMIND** | ⚠️ SAR-only | Quality unmeasurable without real S1 GRD | **~0 ms** (within noise) | Free latency overhead; only fires on `modality=sar` |
| ~~DEFENCE_YOLO~~ | ❌ **Removed** | 1297 FPs / 0 TPs as `battle_damage` | — | Actively degraded mAP |
| ~~DINOV3_LVD~~ | ❌ **Removed** | NaN embeddings on drone-video crops | 715 ms (2.5× SAT) | Silent failure on real data |

**Key finding: DOTA_OBB alone (mAP 0.61) outperforms DOTA_OBB + GROUNDING_DINO together (mAP 0.11)** — adding GDINO causes NMS to suppress DOTA's correct detections. The auto-gate prevents this in production for common DOTA-vocab prompts.

### Sensor-aware Upload page

The dashboard's **Ingest** tab now drives the inference stack from a sensor dropdown:

| Selection | `modality` sent to /detect | `enabled_layers` |
|---|---|---|
| **Optical (RGB)** | `rgb` | `sam3, dota_obb, grounding_dino, dinov3_sat` |
| **Multispectral** | `multispectral` | `sam3, prithvi, dinov3_sat` |
| **Hyperspectral** | `multispectral` (with UI warning) | `sam3, prithvi, dinov3_sat` |
| **SAR** | `sar` | `sam3, terramind` |

The frontend forwards both `modality` and `enabled_layers` (JSON list) on `POST /api/ingest/upload`. The backend stores them in `upload_jobs.metadata` and the worker injects them into every chip's `/detect` metadata.

### How to Run the Comparison Yourself

The full benchmark harness lives under [scripts/](scripts/):

```bash
# 1. Pull real DOTA-v1.0 val + Sen1Floods11 multispectral slices
#    (requires HF_TOKEN in .env). Falls back to synthetic if HF unreachable.
python scripts/fetch_real_datasets.py
python scripts/fetch_eval_datasets.py        # also generates synthetic SAR chips

# 2. Run the full comparison: 4 box configs + 2 segmenter + 3 embedding + 2 SAR.
#    --restart-cmd clears GPU fragmentation between configs (each restart ~50s).
#    --force-grounding-dino bypasses the auto-gate so GDINO's contribution is
#    measurable on common-vocab DOTA prompts.
python scripts/compare_inference_layers.py \
  --url http://172.18.0.2:8001 \
  --slice all --max-chips 30 --repeats 3 \
  --output docs/inference_layer_comparison.md \
  --json-output docs/inference_layer_comparison.json \
  --restart-cmd "docker restart osint-inference-sam3-1" \
  --restart-wait-timeout 180 \
  --force-grounding-dino

# 3. Augmentation-based DINOV3_SAT re-ID stability on still DOTA chips.
python scripts/embedding_stability.py \
  --url http://172.18.0.2:8001 \
  --max-chips 8 --max-instances 15 --n-aug 4 --layers dinov3_sat

# 4. Drone-video cross-frame tracking quality (cv2 + /detect; sidesteps the
#    SAM3 video tracker SDK issues).
python scripts/video_tracking_stability.py \
  --url http://172.18.0.2:8001 \
  --videos sample/53902-476396222_medium.mp4,sample/168811-839864556_medium.mp4 \
  --prompts car,vehicle,person,truck \
  --n-frames 6 --iou-threshold 0.2 --layers dinov3_sat

# 5. Driver unit + smoke tests
cd inference-sam3 && python -m pytest tests/ -q
cd .. && python -m pytest scripts/ --ignore=scripts/eval_datasets/tests/test_inria_fallback.py -q
```

The driver supports a `--dry-run` flag (no live service required) for verifying report generation.

### Per-Slice Test Datasets

| Slice | Source | Size | What it measures |
|---|---|---|---|
| `dota` | `Last-Bullet/DOTAv1.0` val (HF) | 30 chips, 1619 GT boxes | Box-detector quality (mAP@0.5, per-class P/R/F1) |
| `hls_burn` | `KozaMateusz/sen1floods11` S2Hand → HLS 6-band | 10 chips | PRITHVI segmenter latency + chip-level positivity |
| `sen1floods` | Same source, flood masks | 10 chips | PRITHVI flood-head latency |
| `sar` | Synthetic 2-band dB-range TIFFs (real S1 GRD is 480 GB) | 10 chips | TERRAMIND latency overhead (quality requires real GRD) |
| `embedding` | DOTA chips, but only embedding latency reported | 30 chips | DINOV3_SAT and TERRAMIND total/embed times |

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
| AI inference | SAM 3 / 3.1 | facebook/sam3 (image) + facebook/sam3.1 (multiplex video) — native API |
| Worker queue | Celery + Redis | redis:alpine |
| Reverse proxy | Nginx | alpine |
| Frontend | React | 19 |
| Build tool | Vite | 8 |
| CSS | Tailwind CSS | v4 |
| 2D map | react-leaflet | 5 |
| 3D globe | CesiumJS + react-globe.gl | 1.124 |
| Graph viz | react-force-graph-2d | latest |
