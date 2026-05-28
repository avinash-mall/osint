# Environment Variables — Full Reference

**Source of truth:** [.env.example](../../.env.example) ships every variable the platform reads.

Variables below grouped by subsystem. Defaults = the values in `.env.example`.

## Databases & Broker

| Variable | Default | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://neo4j:7687` | Graph database URL |
| `NEO4J_USERNAME` / `NEO4J_PASSWORD` | `neo4j` / `password` | Bolt credentials |
| `POSTGIS_URI` | `postgresql://sentinel:sentinel@postgis:5432/sentinel` | Spatial database |
| `POSTGIS_POOL_MIN` / `POSTGIS_POOL_MAX` | `1` / `10` | Per-process pool sizes |
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker + pubsub |

## Service URLs

| Variable | Default | Description |
|---|---|---|
| `TITILER_URL` | `http://titiler:8080` | Internal tile server |
| `INFERENCE_SAM3_URL` | `http://inference-sam3:8001` | Internal SAM3 service |
| `ONTOLOGY_BACKEND_URL` | `http://backend:8080` | Backend URL inference queries for prompts |

## Volume Paths

| Variable | Default | Description |
|---|---|---|
| `IMAGERY_PATH` / `FMV_PATH` / `DATASET_PATH` | `/data/imagery` / `/data/fmv` / `/data/datasets` | Shared volume mounts |
| `DEM_PATH` | `/data/dem/glo30.vrt` | GLO-30 VRT mosaic for viewshed/LOS; missing → 503 (or fixture if `ANALYTICS_ALLOW_FIXTURES=1`) |
| `OSRM_URL` | `http://osrm:5000` | URL of the OSRM sidecar for routing; unreachable → 503 (or fixture if `ANALYTICS_ALLOW_FIXTURES=1`) |

## Auth & CORS

| Variable | Default | Description |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Allowed browser origins (dev only; production goes through nginx) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `changeme-bootstrap-admin` | Bootstrap admin credentials |
| `SESSION_SECRET` | required (≥ 16 chars) | HMAC key (`openssl rand -hex 32`) |
| `SESSION_TTL_HOURS` | `12` | Cookie lifetime |
| `FORCE_HTTPS` | `0` | Mark `sentinel_session` cookie `Secure` |
| `LDAP_DEFAULT_HOST` / `_PORT` / `_BASE_DN` / `_BIND_DN` | empty | First-boot LDAP defaults (live values in DB) |

## LLM (Ava)

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_BASE` | unset | Local vLLM/Ollama endpoint; unset → LLM disabled |
| `OPENAI_API_KEY` | `dummy` | API key (most local runtimes ignore) |
| `OPENAI_MODEL` | `google/gemma-4-31b-it` | Model name |
| `ENABLE_LLM_DETECTION_CLASSIFICATION` | `true` | Toggle LLM post-classification |

## Detection policy

| Variable | Default | Description |
|---|---|---|
| `DETECTION_TAXONOMY_VERSION` | `open-world-v1` | Stamp written on each detection |
| `DETECTION_THRESHOLD_PROFILE` | `defence_precision` | Informational label |
| `GLOBAL_CONFIDENCE_FLOOR` | `0.35` | Single floor across all classes |
| `HIGH_CONFIDENCE_THRESHOLD` | `0.65` | When to tag `high_confidence` |
| `PER_CLASS_CONFIDENCE_OVERRIDES` | `{}` | JSON map of class-specific floors |
| `REFERENCE_ID_AUTO_THRESHOLD` | `0.85` | Reference Embedding DB auto-identify cosine floor; top-1 candidate above this threshold auto-writes `platform_*` to `object_details`. Read at worker process start — `docker compose restart worker` to apply changes. See [decisions/why-auto-write-with-threshold.md](../decisions/why-auto-write-with-threshold.md). |

## Worker / Imagery

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_SPEED_PROFILE` | `fast_review` | Worker presets for chip count / overlap |
| `INFERENCE_CHIP_SIZE` / `INFERENCE_CHIP_OVERLAP` | `1008` / `252` | SAM3 chip geometry (25% overlap) |
| `MAX_INFERENCE_CHIPS` | `256` | Worker cap (0 = full coverage) |
| `INFERENCE_CHIP_CONCURRENCY` | `1` | Concurrent chip POSTs to SAM3 |
| `INFERENCE_MAX_PENDING_CHIPS` | `32` | Encoded chip queue depth |
| `INFERENCE_CHIP_SPOOL_MAX_BYTES` | `4194304` | Spill encoded chip to disk above this size |
| `INFERENCE_CHIP_TIMEOUT_S` | `600` | Per-request timeout |
| `INFERENCE_READY_TIMEOUT_S` | `300` | Worker wait for inference `/health` |
| `CHANGE_DET_MAX_PIXELS` | `4000000` | Two-pass diff resolution cap |
| `TRACKER_COST_WEIGHTS` | `{}` | JSON tracker cost-function weights |

## Inference (`SAM3_*`)

| Variable | Default | Description |
|---|---|---|
| `DEVICE` | `auto` | `cuda:0` / `cuda:0,cuda:1` / `cpu` |
| `SAM3_WEIGHTS_SOURCE` | `official` | `official` (gated) or `mirror` (open) |
| `SAM3_IMAGE_MODEL_ID` | `facebook/sam3` | Image checkpoint label |
| `SAM3_USE_MULTIPLEX` | `1` | SAM 3.1 multiplex video predictor |
| `SAM3_PRELOAD_MODELS` / `SAM3_PRELOAD_PROFILE` | `0` / empty | Preload at startup |
| `SAM3_COMPILE_IMAGE` / `SAM3_COMPILE_VIDEO` | `0` | Enable `torch.compile` |
| `SAM3_WARM_UP_VIDEO` | `1` | One-frame priming after video load |
| `SAM3_TEXT_THRESHOLD` | `0.50` | Min score for text-prompt detections |
| `SAM3_BOX_THRESHOLD` | `0.25` | Min score for box-prompt detections |
| `SAM3_CATEGORY_THRESHOLD` | `0.40` | Category-presence gate (`0.0` to disable) |
| `SAM3_PRITHVI_OVERLAY_THRESHOLD` | `0.30` | Mask × Prithvi-overlay IoU for label append |
| `SAM3_SAR_CONF_CAP` | `0.85` | Hard cap on SAR confidence |
| `SAM3_OBB_OPENING_KERNEL_PCT` | `0.01` | Morphological opening kernel before `minAreaRect` |
| `SAM3_OBB_MIN_AREA_PX` | `4` | Minimum contour area before HBB fallback |
| `SAM3_DEFAULT_PROMPT_SOURCE` | `precision` | `precision` = bounded built-ins; `ontology`/`backend` = `/api/ontology/default-prompts` fan-out |
| `SAM3_PRECISION_DEFAULT_PROMPTS` | empty | Optional JSON override for bounded defaults, e.g. `{"optical":["vehicle","ship"]}` |
| `SAM3_MAX_PROMPTS_PER_REQUEST` | `64` | Cap on resolved prompts |
| `SAM3_BATCHED_TEXT` / `SAM3_BATCHED_TEXT_CHUNK_SIZE` | `1` / `8` | Batched text prompting |
| `SAM3_LOAD_OPTIONAL_MODELS` | `1` | Master switch — disables individual flags when off |
| `SAM3_LOAD_DINOV3_SAT` | `1` | DINOv3-SAT-L re-ID embeddings |
| `SAM3_LOAD_PRITHVI` | `0` | Prithvi flood/burn |
| `SAM3_LOAD_TERRAMIND` | `1` | TerraMind S1→S2 |
| `SAM3_LOAD_DOTA_OBB` | `1` | DOTA-OBB specialist |
| `SAM3_LOAD_GROUNDING_DINO` | `0` | Grounding-DINO (auto-gated + explicitly enabled per request) |
| `SAM3_LOAD_REMOTECLIP` | `0` | Optional RemoteCLIP verifier; scores existing candidates only |
| `SAM3_LOAD_YOLOE` | `1` | YOLOE-26x FMV tracker |
| `DOTA_OBB_MODEL_ID` | `yolo26m-obb.pt` | Default OBB checkpoint; `yolo11n-obb.pt` for low-VRAM fallback |
| `REMOTECLIP_MODEL_ID` / `REMOTECLIP_ARCH` | `chendelong/RemoteCLIP` / `ViT-B-32` | OpenCLIP-compatible verifier weights + architecture |
| `REMOTECLIP_MARGIN_THRESHOLD` | `0.05` | Semantic margin required for verifier pass |
| `REMOTECLIP_LOCAL_FILES_ONLY` | `1` | Prevent runtime downloads; verifier loads only baked/cache weights |
| `EVIDENCE_MAX_ASPECT_RATIO` | `35` | Backend physical validator aspect-ratio ceiling |
| `EVIDENCE_MIN_MASK_COMPACTNESS` / `EVIDENCE_MIN_VALID_FRACTION` | `0.015` / `0.20` | Backend evidence validator floors |
| `FMV_DEFAULT_PROMPTS` | `vehicle,person,building` | Backend worker PCS fallback when an FMV upload omits prompts |
| `FMV_TRACKER_COST_WEIGHTS` | `{}` | JSON cost weights for FMV track consolidation (`iou`/`emb`/`gap`/`class`) — see [fmv-track-consolidation.md](../backend/fmv-track-consolidation.md) |
| `FMV_TRACK_MIN_IOU` / `FMV_TRACK_MIN_EMB_SIM` | `0.30` / `0.55` | FMV consolidation association gates |
| `FMV_TRACK_MAX_FRAME_GAP_SECONDS` | `2.0` | FMV consolidation temporal gate (bridges window seams) |
| `FMV_TRACK_MATCH_THRESHOLD` / `FMV_TRACK_MERGE_IOU` | `1.50` / `0.55` | FMV consolidation Hungarian reject cutoff / co-temporal merge IoU |
| `SAM3_HF_HUB_OFFLINE` / `SAM3_TRANSFORMERS_OFFLINE` | `1` | Offline mode |
| `HF_TOKEN` | from .env | Required for gated weights |
| `DISABLE_ADDMM_CUDA_LT` | `1` | Route `nn.Linear` off cuBLAS-Lt (A100/cu130 bug) |
| `SENTINEL_DEPLOYMENT_MODE` | `demo` | Login banner posture — `demo` \| `internal` \| `accredited`. Stock clone stays `demo`; operators opt in to a gov/mil banner. Served by `GET /api/system/deployment-mode`. |
| `SENTINEL_DEPLOYMENT_LABEL` | _(per-mode default)_ | Overrides login banner text for `internal` / `accredited` deployments |
| `SENTINEL_AUTH_SUPPORT_CONTACT` | _(unset)_ | Optional admin contact shown on the login screen for LDAP deployments |

## Build-time GPU args

Written by `scripts/configure_host.py` — do not hand-edit:

- `SAM3_CUDA_VERSION`, `SAM3_TORCH_INDEX_URL`, `SAM3_TORCH_VERSION`, `SAM3_TORCHVISION_VERSION`, `SAM3_TORCH_CUDA_ARCH_LIST`, `SAM3_GPU_PROFILE`, `SAM3_UBUNTU_VERSION`

See [gpu-profile-detection.md](gpu-profile-detection.md).

## Cross-references

- [.env.example](../../.env.example) — live source
- [.env.offline.example](../../.env.offline.example) — air-gap variant
- [decisions/disable-addmm-cuda-lt.md](../decisions/disable-addmm-cuda-lt.md)
- [decisions/why-category-presence-gate.md](../decisions/why-category-presence-gate.md)
