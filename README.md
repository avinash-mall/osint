# Sentinel

An open-source GEOINT exploitation platform that ingests satellite imagery and full-motion video, fuses detections into a graph ontology, and surfaces the picture through a dark-mode tactical workstation. Inference is consolidated on **SAM 3 / SAM 3.1** — open-vocabulary segmentation for RGB satellite, multispectral, and SAR imagery — plus **YOLOE-26x-seg** and SAM 3.1 PCS for FMV tracking.

The platform ships as a self-contained Docker Compose stack that can run fully air-gapped: every basemap tile, webfont, and AI weight is baked into the images at build time.

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
│  Celery worker (imagery + default queues)                              │
├──────────┬───────────────┬──────────┬──────────┬───────────┬──────────┤
│  Neo4j   │  PostGIS      │  Redis   │  TiTiler │  Martin   │  Assets  │
│  graph   │  spatial + DB │  broker  │  COG     │  MVT      │  basemap │
└──────────┴───────────────┴──────────┴──────────┴───────────┴──────────┘
```

Only port **3000** is exposed to the host. Every other service runs on the internal compose network.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Graph DB | Neo4j 5.26 + APOC |
| Spatial DB | PostGIS 18-3.6 |
| Cache / broker | Redis 8 alpine |
| Backend | Python 3.11 · FastAPI · Uvicorn · Celery |
| Tile server | TiTiler 2.0.2 — Cloud-Optimised GeoTIFF on-the-fly |
| Vector tiles | Martin 1.9.1 — PostGIS → Mapbox Vector Tiles |
| AI inference | SAM 3 + SAM 3.1 PCS (segmentation + multiplex video tracking) · YOLOE-26x-seg(-pf) (FMV) · DINOv3 ViT-L SAT-493M (re-ID) · Prithvi-EO-2.0 (flood / burn / multi-temporal crop) · TerraMind v1 large (S1↔S2) · DOTA-OBB · Grounding DINO (auto-gated fallback) |
| Frontend | React 19 · TypeScript · Vite 8 · Tailwind utilities · lucide-react icons |
| Map | react-leaflet (2D) · CesiumJS (optional 3D) |
| Auth | Signed session cookies (itsdangerous) · env-bootstrap admin · optional LDAP |
| Reverse proxy | Nginx alpine — TLS termination, tile cache (24 h TTL), HLS streaming |
| Air-gap assets | nginx alpine + baked Carto Dark basemap pyramid (z=0..10) + IBM Plex webfonts |
| GPU | NVIDIA Ampere (sm_80) through Blackwell (sm_120) via per-host CUDA/PyTorch profiles |

---

## Quick Start

```bash
# 1. Detect host GPU + driver and write build settings to .env
python scripts/configure_host.py

# 2. Set HF_TOKEN in .env (required only when SAM3_WEIGHTS_SOURCE=official; gated)
echo "HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" >> .env

# 3. Set a strong session secret and admin password
echo "SESSION_SECRET=$(openssl rand -hex 32)"         >> .env
echo "ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')" >> .env

# 4. Build and start everything (first build ~30–90 min: SAM3 weights + offline basemap)
docker compose up -d --build

# 5. Open the workstation
open http://localhost:3000
```

Sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`. The admin user can configure LDAP from **Admin → Auth · LDAP** for multi-user deployments.

> **LLM (Ava):** point `.env` → `OPENAI_API_BASE` at a local vLLM / Ollama instance. Without it, LLM-backed features (ontology auto-update, AI analysis) return a graceful error; everything else works offline.

> **Air-gap target?** See [docs/offline-deployment.md](docs/offline-deployment.md) for the build-once / load-and-go runbook.

---

## Authentication

The backend gates every mutating (`POST`/`PUT`/`PATCH`/`DELETE`) endpoint behind a signed session cookie. Read endpoints (`GET`) are public.

| Mechanism | Source | When to use |
|---|---|---|
| **Env bootstrap admin** | `ADMIN_USERNAME` / `ADMIN_PASSWORD` in `.env` | Always available; single-tenant or first-boot |
| **LDAP** | `auth_config` row in PostGIS (configured via the Admin UI) | Multi-user / corporate directories |

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/login`    | `{username, password}` → sets `sentinel_session` cookie |
| `POST /api/auth/logout`   | Clears the cookie |
| `GET  /api/auth/me`       | Returns the current `SessionUser` |
| `GET  /api/admin/auth/config` | Read the LDAP settings (admin only) |
| `PUT  /api/admin/auth/config` | Persist LDAP settings (admin only) |
| `POST /api/admin/auth/test`   | Try a username/password bind against current config |
| `POST /api/admin/auth/test-connection` | Probe LDAP TCP/TLS connectivity |

Cookie defaults: `HttpOnly`, `SameSite=Lax`, `max_age` = `SESSION_TTL_HOURS` (12 h), `Secure` when `FORCE_HTTPS=1`. The signing secret comes from `SESSION_SECRET` and must be ≥ 16 chars — the backend refuses to start otherwise.

---

## Frontend Workspaces

The workstation has a 64 px icon rail (expands to 224 px on hover) with four primary workspaces:

| Workspace | Component | What it shows |
|---|---|---|
| **Geoint**     | `GaiaMap` | Common Operating Picture · live detections + imagery passes + asset tracks, time-machine slider, review/provenance/similar panels, change/viewshed/LOS/route analytics |
| **Drone Video**| `FmvPlayer` | HLS-streamed FMV clips with MISB ST 0601 KLV telemetry synced to the map, per-frame detection overlays, prompt-mode picker (PCS / YOLOE) |
| **Link Graph** | `GraphExplorer` | Force-directed graph of Neo4j nodes (Targets, Assets, Observations, Satellites, Bases, LaunchPoints) and their relationships |
| **Admin**      | `AdminScreen` | Ten sub-tabs (see below) |

The **Admin** workspace consolidates operator tooling into one place:

| Tab | Purpose |
|---|---|
| Ontology | Full DB-backed editor for branches, objects, prompts, sensors, per-object icons, and unknown-label triage |
| Upload imagery | Sensor-aware ingest UI (Optical / Multispectral / Hyperspectral / SAR / FMV) with per-modality `enabled_layers` |
| Processing | Live list of analytics + training Celery jobs |
| AI models | Registered detection models with one-click promotion |
| Health dashboard | Inference replicas + active requests + load flags + KPI panels |
| Conf overrides | Per-class confidence override matrix |
| Prompt profiles | Named prompt-profile CRUD |
| Version history | Ontology version log (every edit bumps a version) |
| Health alerts | Operator alerts derived from `/api/health` + failed ingest tasks |
| Auth · LDAP | LDAP configuration form with connection + bind tests |

---

## Inference Service (`inference-sam3`)

A single FastAPI service that bundles every model the platform uses. The runtime pool can hold one of several **profiles**, swapped via `POST /load?profile=<name>` and freed with `POST /unload`:

| Profile | Components |
|---|---|
| `imagery` | `sam3_image`, `dinov3_sat`, `prithvi`, `terramind`, `dota_obb`, `grounding_dino` |
| `fmv`     | `sam3_image`, `sam3_video` (SAM 3.1 multiplex), `dota_obb`, `yoloe` |
| `all`     | Union of both — for 40+ GiB datacenter GPUs, avoids the unload/reload pause when switching |

Individual components are gated by `SAM3_LOAD_*` env flags so memory-constrained GPUs can run a useful subset (see *VRAM budget* below). Each loaded profile is replicated once per available GPU (`DEVICE=auto`) for parallelism.

### Endpoints

| Method | Path | Use |
|---|---|---|
| `GET`  | `/health`        | Lazy-load status, replica list, active requests, model versions |
| `POST` | `/load?profile=` | Force-load `imagery` / `fmv` / `all` |
| `POST` | `/unload`        | Tear everything down and respawn the container (only reliable way to free SAM3's VRAM) |
| `POST` | `/detect`        | Per-chip image segmentation — multipart `image` + JSON `metadata` |
| `POST` | `/detect_video`  | FMV tracking — multipart `video` (or `metadata.video_path`); streams `application/x-ndjson`, one record per frame×track |

### Per-modality contract (image)

| Modality | `metadata.modality` | Chip format | Pipeline |
|---|---|---|---|
| **Optical RGB satellite / aerial** | `rgb` *(default)* | uint8 PNG (1008×1008 from the worker's `chip_to_uint8_rgb`) | SAM3 text prompts (via `metadata.text_prompts` / DB defaults) or box prompts (via `metadata.prompt_boxes`, normalized cxcywh `bbox` and/or 8-pt `obb`) → mask + bbox + OBB + DINOv3-SAT embedding |
| **Multispectral (HLS-6 / S2-L2A)** | `multispectral` | float32 6-band GeoTIFF — Blue, Green, Red, Narrow-NIR, SWIR-1, SWIR-2 (Prithvi `constant_scale=0.0001`) | Resize → Prithvi flood + burn → SAM3 on the RGB preview → optional 3-timestep crop classifier when `metadata.hls_timesteps == 3` |
| **SAR (Sentinel-1 GRD)** | `sar` | float32 2-band GeoTIFF (VV, VH; dB clipped to [-30, 0] then linear-stretched to [0, 1]) | TerraMind S1→S2L2A → RGB preview → SAM3 prompts on the synthetic preview; `confidence` capped at `SAM3_SAR_CONF_CAP=0.85`; output flagged `sar_proxy: true`, `review_status: review_candidate` |

### FMV tracking (`/detect_video`)

Two engines selectable via `metadata.prompt_mode`:

| Mode | Engine | Behavior |
|---|---|---|
| `pcs` *(default)* | SAM 3.1 multiplex (`build_sam3_multiplex_video_predictor`) | Single-prompt-per-session text-prompted tracker; worker fans out one request per prompt and merges the streams |
| `yoloe`           | YOLOE-26x-seg(-pf) | Standalone tracker. Empty `text_prompts` → `-pf` prompt-free; otherwise `-seg` text-prompted. Replaces the deprecated SAM3 AMG path |

Outputs are streamed as NDJSON, one record per frame×track, with optional DINOv3 embedding on the first frame of each track. The worker persists each row into the `fmv_detections` table and publishes a `fmv_detections_complete` event over WebSocket when the run finishes.

### Output schema (per detection)

```json
{
  "class": "building",
  "original_class": "a building",
  "parent_class": "building",
  "bbox": [cx_norm, cy_norm, w_norm, h_norm],
  "obb": [x1, y1, ..., x4, y4],
  "obb_format": "yolo_obb_normalized_xyxyxyxy",
  "obb_source": "mask_min_area_rect",
  "obb_angle_deg": -59.5,
  "obb_area_px": 1861.5,
  "edge_truncated": false,
  "confidence": 0.887,
  "mask_rle": {"size":[H,W],"counts":"<base64 COCO RLE>"},
  "area": 1938,
  "modality": "rgb",
  "task": "sam3_open_vocab_object_detection",
  "embedding": {
    "model": "facebook/dinov3-vitl16-pretrain-sat493m",
    "dim": 1024,
    "fp16_b64": "<base64 fp16 vector>"
  },
  "prithvi_labels": ["water", "crop:corn"],
  "sar_proxy": false,
  "terramind_embedding": null
}
```

The video endpoint emits the same shape per frame×track with extra `frame_index` and `track_id` fields.

### Open-vocabulary detection

Every label SAM 3 emits — text-prompted from the DB ontology or from `metadata.text_prompts` — is accepted as a first-class object class. There is no closed taxonomy; detections are kept unless the operator explicitly raises `GLOBAL_CONFIDENCE_FLOOR` or `PER_CLASS_CONFIDENCE_OVERRIDES`.

`parent_class_for_label` clusters detections into broad open buckets (aircraft, vessel, vehicle, train, building, infrastructure, storage_tank, bridge, harbor, airfield, recreation, vegetation, water, person, animal, food, furniture, household, electronic, tool, clothing, plant, sport, segment, track) and falls back to the **normalized label itself** when no cluster matches.

**Prompt resolution order** (each step skips the rest):

1. `metadata.text_prompts: [...]` — explicit list.
2. Backend ontology defaults for the sensor mapped from `metadata.modality` (`rgb`/`fmv` → optical, `multispectral`/`hyperspectral` → multispectral, `sar` → sar).
3. HTTP 400 if neither yields prompts, or HTTP 503 if the ontology backend is unreachable.

All prompts pass through trim → lowercase → dedupe-preserve-order → cap at `SAM3_MAX_PROMPTS_PER_REQUEST` (default 128).

**Ontology** — Categories, objects, prompts, sensors, and per-object icons live in PostGIS (`ontology_branches`, `ontology_objects`, `ontology_unknown_labels` tables). Edit via the **Admin → Ontology** tab. The seed JSON in `backend/scripts/seed_ontology.py` is consumed once on bootstrap; the DB is the canonical store thereafter. Inference fetches `/api/ontology/default-prompts?sensor=...` and caches per-sensor for 30 s; SIGHUP forces refresh.

**Category-level presence gate** — `SAM3_CATEGORY_THRESHOLD=0.20` (a SegEarth-OV-3-style filter) suppresses prompts whose best mask has weak presence, eliminating hallucinated detections of absent concepts. Set to `0.0` to disable.

### VRAM budget — per-component loader flags

| Flag | Default | Adds (≈ FP16) | Enables |
|---|---|---|---|
| `SAM3_LOAD_OPTIONAL_MODELS` | `1` | — | Master switch — when `0`, the flags below default off |
| `SAM3_LOAD_DINOV3_SAT` | `1` | ~0.6 GB | `embedding` field on every detection (cross-image / cross-frame re-ID) |
| `SAM3_LOAD_PRITHVI` | `1` | ~3 GB | `prithvi_labels` on multispectral chips |
| `SAM3_LOAD_TERRAMIND` | `1` | ~6 GB | SAR S1→S2 generation + `terramind_embedding` |
| `SAM3_LOAD_DOTA_OBB` | `1` | ~0.05 GB | DOTA-v1 oriented-bbox specialist (mAP 0.05 → 0.61 on aerial RGB) |
| `SAM3_LOAD_GROUNDING_DINO` | `1` | ~0.5 GB | Open-vocab text-to-box fallback; auto-gated server-side when every prompt is in the SAM3 + DOTA common vocab |
| `SAM3_LOAD_YOLOE` | `1` | ~1.0 GB (`-pf` + `-seg`) | YOLOE-26x FMV tracker |

> **Removed in v0.10**
> - `SAM3_LOAD_DEFENCE_YOLO` — DEFENCE_YOLO produced 1297 false positives / 0 true positives across 26 DOTA val chips.
> - `SAM3_LOAD_DINOV3_LVD` — DINOV3_LVD emitted **NaN embeddings** on real drone-video crops and was 2.5× slower than DINOV3_SAT with no measured quality advantage. See [docs/video_tracking_stability.md](docs/video_tracking_stability.md).
> - **SAM3 AMG** — the per-pixel `Sam3AutomaticMaskGenerator` path was removed in favor of YOLOE-26x-seg(-pf), which is faster and emits labels directly without a Grounding-DINO labelling pass.

Approximate steady-state VRAM observed on the smoke run (RTX 5070 Ti, 16 GB): SAM 3 + SAM 3.1 video + DINOv3-SAT-L + DOTA-OBB + Grounding-DINO + YOLOE = **~12 GB used**. Loading Prithvi + TerraMind on top pushes close to 22 GB — use a 24 GB+ GPU for the full `imagery` profile.

### Weight sources

| `SAM3_WEIGHTS_SOURCE` | Repo | Gating |
|---|---|---|
| `official` *(default)* | `facebook/sam3` + `facebook/sam3.1` | **Gated** — requires `HF_TOKEN` with approved access |
| `mirror` | `1038lab/sam3` (`sam3.safetensors`) | Open — `HF_TOKEN` optional |

Set `SAM3_MIRROR_REPO_ID` / `SAM3_MIRROR_FILENAME` to point at any other safetensors mirror.

### `inference-sam3` runtime env (compose)

| Variable | Default | Purpose |
|---|---|---|
| `DEVICE` | `auto` | `cuda:0` / `cuda:0,cuda:1` / `cpu` — comma-list creates one replica per device |
| `SAM3_IMAGE_MODEL_ID` | `facebook/sam3` | Image checkpoint label exposed in `/health` |
| `SAM3_USE_MULTIPLEX` | `1` | `1` = SAM 3.1 multiplex predictor; `0` = plain SAM 3 |
| `SAM3_PRELOAD_MODELS` | `0` | `1` → preload at startup (uses `SAM3_PRELOAD_PROFILE`) |
| `SAM3_PRELOAD_PROFILE` | *(empty)* | `imagery` / `fmv` / `all` |
| `SAM3_COMPILE_IMAGE` / `SAM3_COMPILE_VIDEO` | `0` | Enable `torch.compile` (slow first call, faster steady state) |
| `SAM3_WARM_UP_VIDEO` | `1` | Run a one-frame priming pass after video load |
| `SAM3_TEXT_THRESHOLD` | `0.30` | Minimum SAM3 score for text-prompt detections |
| `SAM3_BOX_THRESHOLD` | `0.25` | Minimum SAM3 score for box-prompt detections |
| `SAM3_CATEGORY_THRESHOLD` | `0.20` | Category-level presence gate (set `0.0` to disable) |
| `SAM3_PRITHVI_OVERLAY_THRESHOLD` | `0.30` | Mask × Prithvi-overlay IoU at which the overlay label is appended |
| `SAM3_SAR_CONF_CAP` | `0.85` | Hard cap on confidence for SAR detections (synthetic RGB proxy) |
| `SAM3_OBB_OPENING_KERNEL_PCT` | `0.01` | Morphological opening kernel as fraction of the smaller mask extent before `cv2.minAreaRect` |
| `SAM3_OBB_MIN_AREA_PX` | `4` | Minimum contour area before falling back to HBB |
| `SAM3_MAX_PROMPTS_PER_REQUEST` | `128` | Cap on resolved prompts after dedupe |
| `SAM3_BATCHED_TEXT` / `SAM3_BATCHED_TEXT_CHUNK_SIZE` | `1` / `8` | Batched text prompting for multi-prompt requests |
| `ONTOLOGY_BACKEND_URL` | `http://backend:8080` | Backend URL the inference service queries for sensor-default prompts |
| `SAM3_HF_HUB_OFFLINE` / `SAM3_TRANSFORMERS_OFFLINE` | `1` | Offline mode; flip to `0` only in dev when iterating on the model list |
| `HF_TOKEN` | *from `.env`* | Required when `SAM3_WEIGHTS_SOURCE=official` and for `facebook/dinov3-*` |
| `DISABLE_ADDMM_CUDA_LT` | `1` | Routes `nn.Linear` / `addmm` off cuBLAS-Lt to sidestep a long-running cuBLAS-Lt corruption bug on A100 / cu130 |

Build-time args (`SAM3_CUDA_VERSION`, `SAM3_TORCH_INDEX_URL`, `SAM3_TORCH_VERSION`, `SAM3_TORCHVISION_VERSION`, `SAM3_TORCH_CUDA_ARCH_LIST`, `SAM3_GPU_PROFILE`, `SAM3_UBUNTU_VERSION`) are written by `scripts/configure_host.py`.

### Sample `/detect` invocations

```bash
# A. Open-vocab RGB satellite chip (default modality=rgb)
curl -F image=@chip.png \
     -F 'metadata={"text_prompts":["airplane","ship","oil tanker","helipad"]}' \
     http://inference-sam3:8001/detect | jq '.detections[] | {original_class, confidence}'

# B. Box-prompted segmentation — refine an upstream detector's ROI into a tight SAM3 mask + OBB
curl -F image=@chip.png \
     -F 'metadata={"prompt_boxes":[{"bbox":[0.5,0.5,0.4,0.4],"class":"vessel"}]}' \
     http://inference-sam3:8001/detect | jq '.detections[] | {class, confidence}'

# C. Multispectral 6-band HLS GeoTIFF — adds Prithvi flood + burn overlays
curl -F image=@hls6.tif \
     -F 'metadata={"modality":"multispectral"}' \
     http://inference-sam3:8001/detect | jq '.detections[].prithvi_labels'

# D. SAR (Sentinel-1 GRD VV/VH) — TerraMind generates the optical proxy
curl -F image=@s1grd.tif \
     -F 'metadata={"modality":"sar","text_prompts":["a ship"]}' \
     http://inference-sam3:8001/detect | jq '.detections[] | {original_class, sar_proxy, confidence}'

# E. FMV — SAM 3.1 PCS, single prompt
curl -F video=@clip.mp4 \
     -F 'metadata={"text_prompts":["a person"],"prompt_mode":"pcs","frame_stride":2}' \
     http://inference-sam3:8001/detect_video > tracks.ndjson

# F. FMV — YOLOE prompt-free (AMG replacement)
curl -F video=@clip.mp4 \
     -F 'metadata={"text_prompts":[],"prompt_mode":"yoloe"}' \
     http://inference-sam3:8001/detect_video > tracks.ndjson
```

---

## Imagery Pipeline

The `worker` service consumes the `imagery` Celery queue. A typical satellite-pass ingest:

1. **COG conversion** — `gdal_translate` rewrites the raster to a Cloud-Optimised GeoTIFF.
2. **Catalog** — pass footprint stored as `MULTIPOLYGON` in PostGIS; `SatellitePass` node mirrored in Neo4j.
3. **Chipping** — slices the COG into overlapping 1008×1008 chips (PNG for RGB, GeoTIFF for multispectral/SAR).
4. **Inference dispatch** — `INFERENCE_CHIP_CONCURRENCY` chips are POSTed to `inference-sam3:8001/detect` in parallel through a thread pool.
5. **Georeferencing** — bboxes/OBBs are warped back to Lat/Lon using the source CRS.
6. **Storage** — detections are persisted in PostGIS with mask RLE, embeddings, parent class, original (open-vocab) class, confidence, review status, chip provenance, model/taxonomy version, and coverage metadata.

### Ingest a GeoTIFF

```bash
# Drop a raw raster into the incoming volume (or use a full path inside the container)
curl -X POST http://localhost:3000/api/ingest \
  -H "Content-Type: application/json" \
  -b "sentinel_session=$COOKIE" \
  -d '{"image_url": "/data/imagery/incoming/sentinel2.tif", "sensor_type": "Optical"}'
```

Or upload + ingest in one call from the **Admin → Upload imagery** tab. The sensor dropdown drives `modality` and `enabled_layers`:

| Selection | `modality` sent to `/detect` | `enabled_layers` |
|---|---|---|
| **Optical (RGB)** | `rgb` | `sam3, dota_obb, grounding_dino, dinov3_sat` |
| **Multispectral** | `multispectral` | `sam3, prithvi, dinov3_sat` |
| **Hyperspectral** | `multispectral` (with UI warning) | `sam3, prithvi, dinov3_sat` |
| **SAR** | `sar` | `sam3, terramind` |
| **FMV** | n/a (routed to `/detect_video`) | `sam3_video` or `yoloe` |

### Tile URLs (through the nginx gateway)

```
# COG raster tiles (cached 24 h)
http://localhost:3000/tiles/cog/tiles/{z}/{x}/{y}?url=/data/imagery/processed/pass_cog.tif

# Vector tiles
http://localhost:3000/maps/detections/{z}/{x}/{y}
http://localhost:3000/maps/satellite_passes/{z}/{x}/{y}
http://localhost:3000/maps/ne_countries/{z}/{x}/{y}

# Offline basemap (Carto Dark Matter, z=0..10 baked in)
http://localhost:3000/basemap/{z}/{x}/{y}.png

# FMV HLS segments
http://localhost:3000/fmv/<clip_id>/playlist.m3u8
```

---

## API Reference

The backend exposes 100+ routes. The most commonly used groups:

### Auth · Health · WebSocket

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/health` | Liveness + neo4j/postgis status |
| `WS`   | `/ws` | Push channel — ingest progress, FMV detection completion, ontology updates |

### Graph & Tracks

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/graph` | All Neo4j nodes + edges (limit 1000) |
| `POST` | `/api/graph/neighborhood` | Subgraph around a seed node |
| `GET`  | `/api/geotime/features` | Static features (Bases, LaunchPoints) + asset track history |
| `GET`  | `/api/tracks` | Latest track points |
| `GET`  | `/api/tracks/detections` | Detections grouped into cross-image / cross-frame tracks |
| `POST` | `/api/tracks/detections/reprocess` | Re-run the embedding-based track linker |
| `POST` | `/api/tracks/detections/pin` · `DELETE /api/tracks/detections/{uid}/pin` | Operator pin/unpin |

### Imagery, FMV & Detections

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/imagery` | Satellite passes — filters: `bbox`, `start_time`, `end_time`, `sensor_type` |
| `GET`  | `/api/imagery/{id}/tiles` | TiTiler tile URL template |
| `GET`  | `/api/imagery/{id}/bands` | Per-band statistics |
| `POST` | `/api/ingest` · `POST /api/ingest/upload` · `POST /api/ingest/url` | Three ingest entry points |
| `GET`  | `/api/ingest/uploads` · `GET /api/ingest/jobs/{task_id}` | Upload + job status |
| `POST` | `/api/fmv/clips` | Upload an FMV clip (+ optional `.srt` sidecar); kicks off SAM3/YOLOE tracking |
| `GET`  | `/api/fmv/clips` · `GET /api/fmv/clips/{id}` | List + detail |
| `GET`  | `/api/fmv/clips/{id}/klv` | MISB 0601 telemetry rows |
| `GET`  | `/api/fmv/clips/{id}/detections` | Per-frame detections |
| `GET`  | `/api/detections` · `/api/detections/geojson` | Detections (filterable: `bbox`, `start_time`, `end_time`, `det_class`, `limit`) |
| `GET`  | `/api/detections/classes` | Histogram of seen classes |
| `GET`  | `/api/detections/{id}/details` · `PUT` | Operator-editable detection record |
| `POST` | `/api/detections/manual` | Operator-drawn detection |
| `DELETE` | `/api/detections/{id}` · `DELETE /api/fmv/detections/{id}` | Soft delete |
| `PATCH` | `/api/detections/{id}/tag` | Allegiance tag |
| `PATCH` | `/api/detections/{id}/review` | Review-status update |
| `GET`  | `/api/detections/{id}/similar` · `/api/fmv/detections/{id}/similar` | Embedding-based nearest neighbors |
| `GET`  | `/api/detections/queue` | High-priority review queue |
| `POST` | `/api/detections/resolve` | Entity resolution — links or creates a Target |
| `GET`  | `/api/detections/{id}/candidate-links` · `POST` | Deterministic geo-proximity + class-compatibility candidates against existing Targets. Approve/reject below. (LLM-assisted ranking is a roadmap item.) |
| `POST` | `/api/detection-target-candidates/{id}/approve` · `/reject` | Operator workflow |
| `GET`  | `/api/detections/prithvi-overlays` | Multispectral overlay polygons |

### Inference control

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/inference/load` · `/api/inference/unload` | Proxy `inference-sam3` profile load/unload |
| `GET`  | `/api/inference/health` | Cached inference `/health` |
| `GET`  | `/api/inference/dashboard` | Aggregated KPIs for the Health Dashboard view |
| `GET`  | `/api/inference/confidence-overrides` · `PUT` | Per-class confidence overrides |

### Ontology · Versioning

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/ontology` | Branches + objects (filter by `sensor`) |
| `GET`  | `/api/ontology/version` | Current version cursor |
| `GET`  | `/api/ontology/default-prompts?sensor=` | DB-backed prompt list (used by inference) |
| `GET`  | `/api/ontology/unknown-labels` | LLM-emitted labels awaiting triage |
| `POST` | `/api/ontology/unknown-labels/{label}/assign` | Map to an existing object or create a new one |
| `POST`/`PATCH`/`DELETE` | `/api/ontology/branches[/{id}]` | Branch CRUD |
| `POST`/`PATCH`/`DELETE` | `/api/ontology/objects[/{id}]` | Object CRUD |
| `GET`  | `/api/ontology/prompt-profiles` · `POST` · `PUT /{id}/activate` · `DELETE /{id}` | Named profiles |
| `GET`  | `/api/ontology/version-history` | Audit log |
| `GET`  | `/api/ontology/updates` · `POST /api/ontology/update` | LLM-proposed bulk edits |

### Analytics · Models · Training · Alerts · Feeds

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/analytics/change` · `/viewshed` · `/los` · `/routes` · `/pol` | Spatial analyses |
| `GET`  | `/api/analytics/capabilities` | Reports whether real DEM + routing graph are present |
| `GET`  | `/api/analytics/jobs` | Job log |
| `GET`  | `/api/models` · `/api/models/datasets` | Registered detection models + curated training sets |
| `POST` | `/api/models/datasets` | Register a dataset |
| `POST` | `/api/models/{id}/promote` | Promote a model to "active" |
| `POST` | `/api/training/jobs` · `GET` | Training-job queue |
| `GET`  | `/api/alerts` | Operator alert feed |
| `GET`  | `/api/feeds` · `POST /api/feeds/connect` · `PUT /api/feeds/{id}/status` | Source/feed lifecycle |
| `POST` | `/api/feeds/{id}/events` · `GET` · `GET /api/sources/{id}/events` | Push and fetch feed events |
| `GET`  | `/api/observations` · `/api/timeline/events` | Live observation stream |
| `POST` | `/api/collection/tasks` | Queue a satellite-pass collection task |
| `POST` | `/api/ai/analyze` · `/api/ai/extract` · `/api/ai/link` · `/api/ai/propose-actions` | LLM-backed actions (no-op when `OPENAI_API_BASE` is unset) |
| `GET`  | `/api/actions/proposals` · `POST /api/actions/proposals/{id}/approve` · `/execute` | Action review workflow |

---

## Environment Variables (`.env`)

`.env.example` ships every variable the platform reads. The most operationally relevant:

| Variable | Default | Description |
|---|---|---|
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | `bolt://neo4j:7687` / `neo4j` / `password` | Graph database |
| `POSTGIS_URI` | `postgresql://sentinel:sentinel@postgis:5432/sentinel` | Spatial database |
| `POSTGIS_POOL_MIN` / `POSTGIS_POOL_MAX` | `1` / `10` | Connection pool per process |
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker |
| `TITILER_URL` | `http://titiler:8080` | Internal tile server |
| `INFERENCE_SAM3_URL` | `http://inference-sam3:8001` | Internal SAM3 service |
| `IMAGERY_PATH` / `FMV_PATH` / `DATASET_PATH` | `/data/imagery` / `/data/fmv` / `/data/datasets` | Shared volume mounts |
| `DEM_PATH` | `/data/dem/dem.tif` | DEM GeoTIFF used by viewshed + LOS analytics. Missing file ⇒ endpoints return offline fixtures with `mode: "fixture_no_dem"` |
| `ROUTING_GRAPH_PATH` | `/data/routing/graph.pkl` | Pre-built NetworkX road graph (e.g. `osmnx.graph_from_bbox(...)` then `pickle.dump`). Missing file ⇒ `/api/analytics/routes` falls back to fixtures with `mode: "fixture_no_graph"` |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Allowed browser origins |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `changeme-bootstrap-admin` | Bootstrap admin credentials |
| `SESSION_SECRET` | *(required, ≥ 16 chars)* | HMAC key for session cookies — `openssl rand -hex 32` |
| `SESSION_TTL_HOURS` | `12` | Cookie lifetime |
| `FORCE_HTTPS` | `0` | Mark `sentinel_session` cookie `Secure` |
| `LDAP_DEFAULT_HOST` / `_PORT` / `_BASE_DN` / `_BIND_DN` | *(empty)* | First-boot LDAP defaults (live values stored in DB) |
| `OPENAI_API_BASE` / `OPENAI_API_KEY` / `OPENAI_MODEL` | *(unset)* / `dummy` / `google/gemma-4-31b-it` | Local LLM endpoint |
| `ENABLE_LLM_DETECTION_CLASSIFICATION` | `true` | Toggle LLM post-classification of detections |
| `DETECTION_TAXONOMY_VERSION` | `open-world-v1` | Stamp written on each detection |
| `DETECTION_THRESHOLD_PROFILE` | `recall_review` | Informational label stored on each detection |
| `GLOBAL_CONFIDENCE_FLOOR` | `0.0` | Single floor applied to every class |
| `HIGH_CONFIDENCE_THRESHOLD` | `0.8` | Threshold at which a detection is tagged `high_confidence` |
| `PER_CLASS_CONFIDENCE_OVERRIDES` | `{}` | JSON map of class-specific floors |
| `INFERENCE_SPEED_PROFILE` | `fast_review` | Worker presets for chip count / overlap |
| `INFERENCE_CHIP_SIZE` / `INFERENCE_CHIP_OVERLAP` | `1008` / `252` | Matches SAM3's intended chip geometry (25 % overlap) |
| `MAX_INFERENCE_CHIPS` | `256` | Worker cap; `0` = full coverage, no sampling |
| `INFERENCE_CHIP_CONCURRENCY` | `1` | Concurrent chip POSTs to SAM3 |
| `INFERENCE_MAX_PENDING_CHIPS` | `32` | Encoded chip queue depth |
| `INFERENCE_CHIP_SPOOL_MAX_BYTES` | `4194304` | Spill encoded chips to a temp file when larger |
| `INFERENCE_CHIP_TIMEOUT_S` | `600` | Per-request timeout |

See `.env.example` for the full list including SAM3 inference-side knobs (`SAM3_*`).

---

## Services

| Service | Image | Port | Purpose |
|---|---|---|---|
| `nginx` | `sentinel-nginx:offline` | **3000:80** | Only exposed port; reverse proxy, tile cache (24 h), FMV HLS, offline basemap routing |
| `frontend` | `sentinel-frontend:latest` | internal 3000 | Vite-built React SPA |
| `backend` | `sentinel-backend:latest` | internal 8080 | FastAPI REST + WebSocket |
| `worker` | `sentinel-backend:latest` | — | Celery worker (queues: `imagery`, `default`) |
| `inference-sam3` | `sentinel-inference-sam3:gpu` | internal 8001 | SAM 3 / 3.1, YOLOE, DINOv3, Prithvi, TerraMind, DOTA-OBB, Grounding-DINO |
| `neo4j` | `neo4j:5.26.26-community-ubi10` | — | Graph ontology + APOC |
| `postgis` | `postgis/postgis:18-3.6` | — | Spatial catalog, detections, auth, ontology |
| `redis` | `redis:8-alpine` | — | Celery broker |
| `titiler` | `ghcr.io/developmentseed/titiler:2.0.2` | internal 8080 | COG tile server |
| `martin` | `ghcr.io/maplibre/martin:1.9.1` | internal 3000 | PostGIS → MVT |
| `assets` | `sentinel-assets:offline` | internal 80 | Offline Carto Dark basemap (z=0..10) + IBM Plex webfonts |
| `llm-local-proxy` *(profile `llm-proxy`)* | `alpine/socat:1.8.0.3` | host 18001 | Optional TCP forwarder so containers can reach a host-side vLLM/Ollama on `127.0.0.1` |

---

## GPU Portability

`scripts/configure_host.py` reads `nvidia-smi`, resolves the matching CUDA/PyTorch/TorchVision/arch-list profile (Turing through Blackwell), and writes a **SENTINEL GENERATED GPU CONFIG** block into `.env`. Do not hand-edit those values or copy them between machines.

```bash
python scripts/configure_host.py
docker compose up -d --build
```

The preflight fails before build when a profile requires a newer host driver. Examples:

| GPU | Profile | CUDA | PyTorch | TorchVision |
|---|---|---|---|---|
| Turing (T4, sm_75) | `turing` | 12.4 | 2.6 | 0.21 |
| Ampere (A100, sm_80 / A40, sm_86) | `ampere` | 12.4 | 2.6 | 0.21 |
| Hopper (H100, sm_90) | `hopper` | 12.6 | 2.6 | 0.21 |
| Blackwell (RTX 50-series, sm_120) | `blackwell` | 12.8 | 2.7 | 0.22 |

Rerun the preflight after upgrading the GPU or NVIDIA driver.

---

## Inference Layer Comparison

The 7-layer inference stack was systematically benchmarked on real public data (DOTA-v1.0 val for RGB box-detection quality, Sen1Floods11 for multispectral Prithvi, NASA drone footage for video re-ID, synthetic 2-band SAR for TerraMind latency). Reports under [docs/](docs/):

- [docs/inference_layer_comparison.md](docs/inference_layer_comparison.md) — image-stack mAP / latency tables
- [docs/embedding_stability.md](docs/embedding_stability.md) — DINOv3-SAT augmentation re-ID quality
- [docs/video_tracking_stability.md](docs/video_tracking_stability.md) — drone-video cross-frame tracking

### Headline results (RTX 5070 Ti, 16 GB)

| Layer | Verdict | Quality | Cost / chip | Notes |
|---|---|---|---|---|
| **SAM 3 (base)** | ✅ Foundation | mAP 0.05 alone on DOTA val | 590 ms | Required for masks |
| **DOTA_OBB** | ✅ **Keep** | mAP **0.05 → 0.61** (aircraft recall 0 % → 92 %, naval 0.6 % → 21 %) | **+50 ms** | Single biggest quality win |
| **GROUNDING_DINO** | ✅ Keep — **auto-gated** | +0.01 mAP when forced | +115 ms (skipped 100 % on common-vocab prompts) | Server-side gate at [grounding_dino_gate.py](inference-sam3/grounding_dino_gate.py) |
| **PRITHVI** | ✅ Keep | Per-pixel flood/burn (chip-level metric N/A through current API) | **+20 ms** | Only specialist for multispectral |
| **DINOV3_SAT** | ✅ Keep | Top-1 re-ID **100 %** on stills, SEP **+0.22** on 1440p drone video | +217 ms / +293 ms embed | Only embedding worth keeping |
| **TERRAMIND** | ⚠️ SAR-only | Quality unmeasurable without real S1 GRD | **~0 ms** (within noise) | Only fires on `modality=sar` |
| **YOLOE** | ✅ FMV | Replaces SAM3 AMG; emits labels directly | comparable to SAM3.1 PCS | Both `-pf` (prompt-free) and `-seg` (text) |
| ~~DEFENCE_YOLO~~ | ❌ **Removed** | 1297 FPs / 0 TPs as `battle_damage` | — | Actively degraded mAP |
| ~~DINOV3_LVD~~ | ❌ **Removed** | NaN embeddings on drone-video crops | 715 ms (2.5× SAT) | Silent failure on real data |
| ~~SAM3 AMG~~ | ❌ **Removed** | Required Grounding-DINO for labels | — | YOLOE-26x-seg(-pf) replaces it |

**Key finding:** DOTA_OBB alone (mAP 0.61) outperforms DOTA_OBB + GROUNDING_DINO together (mAP 0.11) on common-vocab DOTA prompts — adding GDINO causes NMS to suppress DOTA's correct detections. The auto-gate prevents this in production.

### How to run the comparison yourself

The full benchmark harness lives under [scripts/](scripts/):

```bash
# 1. Pull real DOTA-v1.0 val + Sen1Floods11 multispectral slices.
#    The default path fails honestly when data is unavailable.
python scripts/fetch_real_datasets.py
python scripts/fetch_eval_datasets.py
#    For deterministic test/demo fixtures only:
# python scripts/fetch_eval_datasets.py --synthetic-fixtures

# 2. Run the full comparison: 4 box configs + 2 segmenter + 3 embedding + 2 SAR.
python scripts/compare_inference_layers.py \
  --url http://172.18.0.2:8001 \
  --slice all --max-chips 30 --repeats 3 \
  --output docs/inference_layer_comparison.md \
  --json-output docs/inference_layer_comparison.json \
  --restart-cmd "docker restart osint-inference-sam3-1" \
  --restart-wait-timeout 180 \
  --force-grounding-dino

# 3. Augmentation-based DINOv3-SAT re-ID stability on still DOTA chips.
python scripts/embedding_stability.py \
  --url http://172.18.0.2:8001 \
  --max-chips 8 --max-instances 15 --n-aug 4 --layers dinov3_sat

# 4. Drone-video cross-frame tracking quality.
python scripts/video_tracking_stability.py \
  --url http://172.18.0.2:8001 \
  --videos sample/53902-476396222_medium.mp4,sample/168811-839864556_medium.mp4 \
  --prompts car,vehicle,person,truck \
  --n-frames 6 --iou-threshold 0.2 --layers dinov3_sat

# 5. Driver unit + smoke tests
cd inference-sam3 && python -m pytest tests/ -q
cd .. && python -m pytest scripts/ -q
```

Pass `--dry-run` to verify report generation without a live service.

### Per-slice test datasets

| Slice | Source | Size | What it measures |
|---|---|---|---|
| `dota` | `Last-Bullet/DOTAv1.0` val (HF) | 30 chips, 1619 GT boxes | Box-detector quality (mAP@0.5, per-class P/R/F1) |
| `hls_burn` | `KozaMateusz/sen1floods11` S2Hand → HLS 6-band | 10 chips | PRITHVI segmenter latency + chip-level positivity |
| `sen1floods` | Same source, flood masks | 10 chips | PRITHVI flood-head latency |
| `sar` | Synthetic 2-band dB-range TIFFs | 10 chips | TERRAMIND latency overhead |
| `embedding` | DOTA chips, embedding latency only | 30 chips | DINOV3_SAT and TERRAMIND total/embed times |

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

# Re-seed the DB ontology from the JSON snapshot
python backend/scripts/seed_ontology.py
```

For day-to-day inference iteration the offline image bakes weights into the container. Layer a `docker-compose.dev.yml` with a writable `sam3_models` volume to restore the "first run downloads, subsequent runs reuse" loop — see [docs/offline-deployment.md](docs/offline-deployment.md#dev-override).

---

## Air-Gap Deployment

The full build → save → load → run sequence for disconnected sites is documented in [docs/offline-deployment.md](docs/offline-deployment.md). Highlights:

- Single command on the connected host: `docker compose build` (~30–90 min including ~3 GB basemap fetch + ~18 GB SAM3 weights).
- All upstream images pinned to specific digests for byte-for-byte reproducibility.
- Runtime DNS / 443 traffic verified zero via `tcpdump` and `docker network create --internal`.
- Self-hosted Carto Dark Matter basemap (z=0..10), IBM Plex webfonts, SIL OFL 1.1 license bundle.

---

## Licenses

| Component | License | Gating |
|---|---|---|
| SAM 3 / SAM 3.1 code + weights | [Meta SAM License](https://github.com/facebookresearch/sam3/blob/main/LICENSE) — read before commercial use | **Gated** (or use the `1038lab/sam3` mirror) |
| DINOv3 weights | [Meta DINOv3 License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/) | **Gated** |
| YOLOE weights | AGPL-3.0 | Open |
| Prithvi-EO-2.0 weights | Apache 2.0 | Open |
| TerraMind v1 weights | Apache 2.0 | Open |
| Grounding-DINO weights | Apache 2.0 | Open |
| Carto basemap tiles | © OpenStreetMap contributors · © CARTO (CC-BY) | Attribution required (rendered in the basemap layer credits) |
| IBM Plex fonts | SIL OFL 1.1 | Served at `/assets/LICENSE.txt` |

---

## Component Details

| Component | Technology | Version |
|---|---|---|
| Graph DB | Neo4j | 5.26.26 |
| Spatial DB | PostGIS | 18-3.6 |
| GDAL | gdal-bin | 3.10.3 |
| Backend | Python / FastAPI | 3.11 |
| Tile server | TiTiler | 2.0.2 |
| Vector tiles | Martin | 1.9.1 |
| AI inference | SAM 3 + SAM 3.1 | `facebook/sam3` (image) + `facebook/sam3.1` (multiplex video) — native API |
| FMV tracker | YOLOE | 26x-seg + 26x-seg-pf |
| Worker queue | Celery + Redis | redis:8-alpine |
| Reverse proxy | Nginx | alpine |
| Frontend | React | 19 |
| Build tool | Vite | 8 |
| 2D map | react-leaflet | latest |
| 3D globe | CesiumJS | optional |
| Auth | itsdangerous + ldap3 | |
