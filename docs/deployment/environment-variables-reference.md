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
| `LAE_DINO_URL` | `http://inference-lae:8010` | Internal LAE-DINO sidecar URL the `grounding_dino` layer calls. See [lae-dino-sidecar.md](../inference/lae-dino-sidecar.md) |
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
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / required | Bootstrap admin credentials; Compose fails fast when `ADMIN_PASSWORD` is unset/empty |
| `SESSION_SECRET` | required (≥ 16 chars) | HMAC key (`openssl rand -hex 32`); Compose fails fast when unset/empty |
| `SESSION_TTL_HOURS` | `12` | Cookie lifetime |
| `FORCE_HTTPS` | `0` | Mark `sentinel_session` cookie `Secure` |
| `LDAP_DEFAULT_HOST` / `_PORT` / `_BASE_DN` / `_BIND_DN` | empty | First-boot LDAP defaults (live values in DB) |
| `MAX_UPLOAD_BYTES` | `10737418240` | Shared multipart upload cap (10 GiB default); over-limit writes return 413 and delete partial files |

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
| `PER_CLASS_CONFIDENCE_OVERRIDES` | `{}` | JSON map of class-specific floors. Merges on top of `DEFAULT_PER_CLASS_THRESHOLDS` (T1.5) — see [why-transportation-floor-raised.md](../decisions/why-transportation-floor-raised.md) |
| `LABEL_VERIFIER_MARGIN_FLOOR` | `0.10` | Minimum `semantic_margin` for a detection to be promoted to `label_quality="verified"`. Generic verifier plumbing: no verifier currently emits `semantic_margin` (the RemoteCLIP verifier was removed), so labels stay `inferred`/`generic` until a future verifier feeds it. See [why-generic-labels-when-unverified.md](../decisions/why-generic-labels-when-unverified.md) |
| `REFERENCE_ID_AUTO_THRESHOLD` | `0.85` | Reference Embedding DB auto-identify cosine floor; top-1 candidate above this threshold auto-writes `platform_*` to `object_details`. Read at worker process start — `docker compose restart worker` to apply changes. See [decisions/why-auto-write-with-threshold.md](../decisions/why-auto-write-with-threshold.md). |

## Worker / Imagery

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_SPEED_PROFILE` | `fast_review` | Worker presets for chip count / overlap |
| `INFERENCE_CHIP_SIZE` / `INFERENCE_CHIP_OVERLAP` | `1008` / `252` | SAM3 chip geometry (25% overlap) |
| `INFERENCE_SMALL_OBJECT_CHIP_SIZE` | `0` | When `>0` and `!= INFERENCE_CHIP_SIZE`, runs a second finer chip pass (e.g. `504`) giving small targets more pixels-per-object. `0` = off. Shares the main dedupe index |
| `INFERENCE_SMALL_OBJECT_OVERLAP` | `128` | Overlap for the small-object second pass |
| `INFERENCE_SMALL_OBJECT_MAX_CHIPS` | `256` | Chip cap for the small-object second pass (defaults to the profile's `max_chips`) |
| `INFERENCE_FULL_SCENE_PASS` | `0` | When `1`, appends ONE extra pass over the whole image read decimated (COG overviews) to catch objects larger than a chip (runways, piers). Shares the dedupe index; adds exactly 1 window to progress. `0` = off |
| `DEDUPE_METHOD` | `nms` | Cross-chip dedupe in overlap zones: `nms` (keep best box) or `wbf` (weighted box fusion). NMS is default — see `docs/benchmarks/chip-dedupe-nms-vs-wbf-2026-06-12.md` |
| `WBF_IOU_THRESHOLD` | `0.55` | IoU cluster threshold for the WBF fuser (only when `DEDUPE_METHOD=wbf`) |
| `WBF_EXPECTED_MODELS` | `2` | Expected detector count for WBF score weighting (only when `DEDUPE_METHOD=wbf`) |
| `MAX_INFERENCE_CHIPS` | `256` | Worker cap (0 = full coverage) |
| `INFERENCE_CHIP_CONCURRENCY` | `1` | Concurrent chip POSTs to SAM3 |
| `INFERENCE_MAX_PENDING_CHIPS` | `32` | Encoded chip queue depth (in-flight ceiling) |
| `INFERENCE_MIN_PENDING_CHIPS` | `4` | Floor for the adaptive back-off so it never starves the GPU-replica pool — **auto-set to GPU count by `configure_host.py`** |
| `INFERENCE_CHIP_SPOOL_MAX_BYTES` | `4194304` | Spill encoded chip to disk above this size |
| `INFERENCE_CHIP_TIMEOUT_S` | `600` | Per-request timeout |
| `INFERENCE_READY_TIMEOUT_S` | `300` | Worker wait for inference `/health` |
| `ALLOW_REMOTE_IMAGERY_URLS` | `0` | Enables worker-side HTTP(S) imagery fetch only on connected/prep hosts |
| `REMOTE_IMAGERY_ALLOWED_HOSTS` | empty | Optional comma-separated host allowlist for remote imagery fetch |
| `REMOTE_IMAGERY_MAX_BYTES` | `10737418240` | Remote imagery download cap before deleting partial files |
| `CHANGE_DET_MAX_PIXELS` | `4000000` | Two-pass diff resolution cap |
| `TRACKER_COST_WEIGHTS` | `{}` | JSON tracker cost-function weights |
| `LIVE_DETECTIONS_STREAM` | `1` | Embed map-ready features in the per-chip `detections_partial` WS event so the map renders detections live (chip-by-chip) instead of after the whole pass. `0` = count-only events + end-of-pass load. See [decisions/why-live-streaming-detections.md](../decisions/why-live-streaming-detections.md) |
| `LIVE_DETECTIONS_MAX_FEATURES` | `400` | A chip with more detections streams counts only (the end-of-pass load still reconciles), bounding the WS message size |

## Graph analytics (Phase 6)

Tuning for the city2graph-inherited beat builders. See [operations/celery-beat-schedule.md](../operations/celery-beat-schedule.md), [decisions/why-proximity-colocation-graph.md](../decisions/why-proximity-colocation-graph.md), [decisions/why-gnn-link-prediction.md](../decisions/why-gnn-link-prediction.md).

| Variable | Default | Description |
|---|---|---|
| `COLOCATION_BUILDER_INTERVAL_S` | `21600` | Cadence of `worker.tick_colocation_builder` (`COLOCATED_WITH` edges) |
| `COLOCATION_WINDOW_DAYS` | `30` | Only detections created within this window feed the co-location graph |
| `COLOCATION_MAX_NODES` | `2000` | Cap on detection centroids per build |
| `COLOCATION_METHOD` | `knn` | Proximity method: `knn` / `delaunay` / `gabriel` / `relative_neighborhood` / `mst` / `fixed_radius` |
| `COLOCATION_KNN_K` | `6` | Neighbour count for the `knn` method |
| `COLOCATION_RADIUS_M` | `3000` | Radius cap (metres); the distance bound for kNN and the radius for `fixed_radius` |
| `GNN_LINK_PREDICTION_INTERVAL_S` | `86400` | Cadence of `worker.tick_gnn_link_prediction` (skips cleanly when torch is absent) |
| `GNN_LINK_TOP_K` | `50` | Max advisory `GNN_SUGGESTED_LINK` edges written per run |
| `GNN_SNAPSHOT_LIMIT` | `1500` | Node bound on the entity-graph snapshot the GNN trains on |

## Inference (`SAM3_*`)

| Variable | Default | Description |
|---|---|---|
| `DEVICE` | `auto` | `cuda:0` / `cuda:0,cuda:1` / `cpu` |
| `SAM3_WEIGHTS_SOURCE` | `official` | `official` (gated) or `mirror` (open) |
| `SAM3_IMAGE_MODEL_ID` | `facebook/sam3` | Image checkpoint label |
| `SAM3_USE_MULTIPLEX` | `1` | SAM 3.1 multiplex video predictor |
| `SAM3_PRELOAD_MODELS` / `SAM3_PRELOAD_PROFILE` | `0` / empty | Preload at startup |
| `SAM3_LOAD_POLICY` | `hot` (dynamic on <24 GiB) | VRAM-gated loading policy written by `configure_host.py`. `dynamic` loads one per-modality profile at a time + drops dead-weight detectors. See [why-dynamic-modality-loading-on-tight-vram.md](../decisions/why-dynamic-modality-loading-on-tight-vram.md) |
| `SAM3_RESTING_PROFILE` | `imagery` (`imagery_rgb` on dynamic) | Profile the lifespan loads at startup for the healthcheck |
| `SAM3_COMPILE_IMAGE` / `SAM3_COMPILE_VIDEO` | `0` | Enable `torch.compile` |
| `SAM3_WARM_UP_VIDEO` | `1` | One-frame priming after video load |
| `SAM3_TEXT_THRESHOLD` | `0.50` | Min score for text-prompt detections |
| `SAM3_BOX_THRESHOLD` | `0.25` | Min score for box-prompt detections |
| `SAM3_CATEGORY_THRESHOLD` | `0.40` | Category-presence gate (`0.0` to disable) |
| `SAM3_PRESENCE_MODE` | `both` | Presence-gate composition: `max` (legacy only), `ratio` (SegEarth-OV3 distribution gate only), `both` (DEFAULT — both must pass). See [why-segearth-presence-filter.md](../decisions/why-segearth-presence-filter.md) |
| `SAM3_PRESENCE_RATIO_FLOOR` | `1.8` | Minimum `max_score / mean_score` ratio for a prompt to pass the SegEarth-OV3 distribution gate. Higher = stricter (kills more diffuse responses). `0.0` disables the ratio gate even in mode `both` |
| `SAM3_PRESENCE_RATIO_EPS` | `0.05` | Floor for the denominator when computing the presence ratio; prevents division-by-zero when SAM3 emits near-zero mean scores |
| `SAM3_GATE_SCORE_FLOOR` | `0.05` | Score floor the batched text path postprocesses at so the presence gate sees the full score distribution (not just above-`score_threshold` survivors). `0.0` = exact single-prompt parity; raise if profiling shows the extra masks cost too much. See [why-batched-presence-gate-floor.md](../decisions/why-batched-presence-gate-floor.md) |
| `SAM3_SAR_CONF_CAP` | `0.85` | Hard cap on SAR confidence |
| `SAM3_OBB_OPENING_KERNEL_PCT` | `0.01` | Morphological opening kernel before `minAreaRect` |
| `SAM3_OBB_MIN_AREA_PX` | `4` | Minimum contour area before HBB fallback |
| `SAM3_FUSION_MODE` | `wbf` | Cross-detector fuser: `wbf` (Weighted Boxes Fusion, default) or `nms` (legacy mask-aware NMS). See [why-wbf-over-nms.md](../decisions/why-wbf-over-nms.md) |
| `SAM3_WBF_WEIGHTS` | empty | JSON `{source_layer: float}` overriding the per-detector trust weights (defaults: sam3=0.5, dota_obb=1.0, grounding_dino=0.3, yoloe=0.5, sar_cfar=0.7, mvrsd=1.0) |
| `SAM3_WBF_IOU` | `0.55` | IoU threshold for WBF cluster matching (also reused as the fallback NMS IoU) |
| `SAM3_WBF_SKIP_THRESHOLD` | `0.05` | Per-input minimum confidence before WBF; below-threshold detections are dropped pre-fusion |
| `SAM3_DEFAULT_PROMPT_SOURCE` | `precision` | `precision` = bounded built-ins; `ontology`/`backend` = `/api/ontology/default-prompts` fan-out |
| `SAM3_PRECISION_DEFAULT_PROMPTS` | empty | Optional JSON override for bounded defaults, e.g. `{"optical":["vehicle","ship"]}` |
| `SAM3_MAX_PROMPTS_PER_REQUEST` | `64` | Cap on resolved prompts |
| `SAM3_BATCHED_TEXT` / `SAM3_BATCHED_TEXT_CHUNK_SIZE` | `1` / `8` | Batched text prompting |
| `SAM3_LOAD_OPTIONAL_MODELS` | `1` | Master switch — disables individual flags when off |
| `SAM3_LOAD_DINOV3_SAT` | `1` | DINOv3-SAT-L re-ID embeddings |
| `SAM3_EMBED_BATCH_SIZE` | `32` | Crops per DINOv3 forward in the batched embedding path — **VRAM-tiered, auto-set by `configure_host.py`** (shrunk to 16 when a GPU co-tenant is detected) |
| `SAM3_GPU_MEMORY_FRACTION` | `0` | Per-process VRAM ceiling (fraction of each GPU's total) — **manual only; `configure_host.py` no longer auto-sets it** (the auto-detection misfired and throttled dedicated cards — see [why-removed-auto-vram-cap](../decisions/why-removed-auto-vram-cap.md)). `0`/unset = no cap. Set by hand on a genuine shared-GPU host so an over-budget alloc OOMs cleanly instead of illegal-accessing the neighbour |
| `SAM3_LOAD_TERRAMIND` | `1` | TerraMind S1→S2 |
| `SAM3_LOAD_DOTA_OBB` | `1` | DOTA-OBB specialist |
| `SAM3_LOAD_GROUNDING_DINO` | `0` | `grounding_dino` open-vocab layer — now backed by the **LAE-DINO** sidecar (auto-gated + explicitly enabled per request). Enabling it needs the `inference-lae` service (`docker compose --profile lae up`). See [why-lae-dino-replaces-grounding-dino.md](../decisions/why-lae-dino-replaces-grounding-dino.md) |
| `GROUNDING_DINO_THRESHOLD` / `GROUNDING_DINO_TEXT_THRESHOLD` | `0.30` / `0.25` | LAE-DINO box / text score floors forwarded to the sidecar |
| `LAE_VISIBLE_DEVICES` | (generated) | GPU(s) for the LAE sidecar — auto-set by `configure_host.py` (dedicated card at ≥3 free, else shares SAM3's last card). See [why-auto-gpu-division.md](../decisions/why-auto-gpu-division.md) |
| `SAM3_LOAD_YOLOE` | `1` | YOLOE-26x FMV tracker |
| `SAM3_LOAD_MVRSD` | `1` | MVRSD military-vehicle specialist (fine-tuned `yolo11m` detect, 5 classes, sub-meter optical RGB). Default-on, tied to `SAM3_LOAD_OPTIONAL_MODELS` like DOTA-OBB; loads with the `imagery_rgb` profile and runs on every RGB `/detect` via the default-True `_layer_active` filter. Off only if `SAM3_LOAD_OPTIONAL_MODELS=0` or this is `0`; per-request opt-out by omitting `mvrsd` from a non-empty `enabled_layers`. See [why-mvrsd-military-vehicle-specialist.md](../decisions/why-mvrsd-military-vehicle-specialist.md) |
| `MVRSD_CONF` | `0.25` | MVRSD confidence floor |
| `MVRSD_WEIGHTS_URL` | (empty) | **Build ARG.** GitHub release-asset URL for the MVRSD weight; baked to `/models/mvrsd/mvrsd_yolo11m.pt` at build time (hard rule #8). Empty = bake skipped + runner honour-gates. Set by the orchestrator after upload |
| `MVRSD_WEIGHTS_PATH` | `/models/mvrsd/mvrsd_yolo11m.pt` | Runtime override for the in-container MVRSD weight path |
| `DOTA_OBB_MODEL_ID` | `yolo26m-obb.pt` | Default OBB checkpoint; `yolo11n-obb.pt` for low-VRAM fallback |
| `EVIDENCE_MAX_ASPECT_RATIO` | `35` | Backend physical validator aspect-ratio ceiling |
| `EVIDENCE_MIN_MASK_COMPACTNESS` / `EVIDENCE_MIN_VALID_FRACTION` | `0.015` / `0.20` | Backend evidence validator floors |
| `FMV_DEFAULT_PROMPTS` | `vehicle,person,building` | Backend worker PCS fallback when an FMV upload omits prompts |
| `FMV_PROBE_TIMEOUT_S` | `30` | `ffprobe` timeout during synchronous FMV upload cataloging |
| `FMV_TRANSCODE_TIMEOUT_S` | `900` | `ffmpeg` HLS stream-copy timeout before falling back to the raw clip |
| `FMV_TRACKER_COST_WEIGHTS` | `{}` | JSON cost weights for FMV track consolidation (`iou`/`emb`/`gap`/`class`) — see [fmv-track-consolidation.md](../backend/fmv-track-consolidation.md) |
| `FMV_TRACK_MIN_IOU` / `FMV_TRACK_MIN_EMB_SIM` | `0.30` / `0.55` | FMV consolidation association gates |
| `FMV_TRACK_MAX_FRAME_GAP_SECONDS` | `2.0` | FMV consolidation temporal gate (bridges window seams) |
| `FMV_TRACK_MATCH_THRESHOLD` / `FMV_TRACK_MERGE_IOU` | `1.50` / `0.55` | FMV consolidation Hungarian reject cutoff / co-temporal merge IoU |
| `SAM3_HF_HUB_OFFLINE` / `SAM3_TRANSFORMERS_OFFLINE` | `1` | Offline mode |
| `HF_TOKEN` | empty | Optional build-time token for gated weights; never commit a real token |
| `DISABLE_ADDMM_CUDA_LT` | `1` | Route `nn.Linear` off cuBLAS-Lt (A100/cu130 bug) |
| `SENTINEL_DEPLOYMENT_MODE` | `demo` | Login banner posture — `demo` \| `internal` \| `accredited`. Stock clone stays `demo`; operators opt in to a gov/mil banner. Served by `GET /api/system/deployment-mode`. |
| `SENTINEL_DEPLOYMENT_LABEL` | _(per-mode default)_ | Overrides login banner text for `internal` / `accredited` deployments |
| `SENTINEL_AUTH_SUPPORT_CONTACT` | _(unset)_ | Optional admin contact shown on the login screen for LDAP deployments |

## Build-time GPU args

Written by `scripts/configure_host.py` — do not hand-edit:

- `SAM3_CUDA_VERSION`, `SAM3_TORCH_INDEX_URL`, `SAM3_TORCH_VERSION`, `SAM3_TORCHVISION_VERSION`, `SAM3_TORCH_CUDA_ARCH_LIST`, `SAM3_GPU_PROFILE`, `SAM3_UBUNTU_VERSION`
- **GPU division (generated):** `SAM3_VISIBLE_DEVICES`, `LAE_VISIBLE_DEVICES`, `SAM3_SERIALIZE_FORWARDS` (multi-replica only), `INFERENCE_CHIP_CONCURRENCY`/`INFERENCE_MIN_PENDING_CHIPS` (SAM3-allocated count).

**Operator input (preserved, NOT generated):** `SENTINEL_RESERVED_GPUS` — comma list of GPU indices to keep away from Sentinel (e.g. `0,1` for a vLLM co-tenant). `configure_host` divides the remaining cards between the services. See [why-auto-gpu-division.md](../decisions/why-auto-gpu-division.md).

See [gpu-profile-detection.md](gpu-profile-detection.md).

## Cross-references

- [.env.example](../../.env.example) — live source
- [.env.offline.example](../../.env.offline.example) — air-gap variant
- [decisions/disable-addmm-cuda-lt.md](../decisions/disable-addmm-cuda-lt.md)
- [decisions/why-category-presence-gate.md](../decisions/why-category-presence-gate.md)
- [decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
