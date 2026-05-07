# SAM3-CDX Pretrained Inference Endpoint Plan

## 1. Summary

Build a new pretrained-only inference provider named `sam3-cdx` that combines:

- Meta SAM 3/SAM 3.1 for promptable open-vocabulary detection and segmentation.
- IBM/NASA Prithvi-EO-2.0-600M-TL as the primary Earth-observation feature backbone for geospatial chips.
- IBM/NASA Prithvi-EO-2.0-300M as the lighter fallback/backbone option.
- Meta DINOv3 ViT-7B as an RGB visual feature extractor/reranker.

The endpoint must not train or fine-tune any model. It will run pretrained weights only and expose the same service shape as the existing inference providers: `POST /detect` receives an image chip and optional JSON metadata, then returns normalized detections. The backend will treat `sam3-cdx` as a normal provider and will store its detections through the existing PostGIS/Neo4j pipeline.

Important model-interface facts verified from the upstream pages:

- SAM 3 is a promptable segmentation model that can detect and segment all instances matching a text or visual prompt; the current repo also lists SAM 3.1 checkpoint updates released on 2026-03-27. Source: https://github.com/facebookresearch/sam3
- Prithvi-EO-2.0 uses ViT/MAE-style pretrained Earth-observation backbones, with TL variants accepting temporal and geolocation metadata; the EO inputs are six HLS bands in order: Blue, Green, Red, Narrow NIR, SWIR 1, SWIR 2. Source: https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL
- DINOv3 `facebook/dinov3-vit7b16-pretrain-lvd1689m` is exposed through Transformers as an image feature extraction model, returning embeddings rather than class labels. Source: https://huggingface.co/facebook/dinov3-vit7b16-pretrain-lvd1689m

Because Prithvi and DINOv3 are not pretrained label classifiers, the plan uses a deterministic label registry plus SAM3 text prompts as the label source. Prithvi and DINOv3 are used for feature extraction, label confidence calibration, dedupe support, and optional reranking; they do not invent labels by themselves.

## 2. Target Architecture

### 2.1 Service Topology

Add a new containerized FastAPI service:

```text
backend/worker
  |
  | POST /detect image chip + metadata
  v
inference-sam3-cdx
  |
  |-- SAM3 prompt loop: label -> masks, boxes, SAM score
  |-- Prithvi EO feature extraction: multispectral/metadata-aware geospatial context
  |-- DINOv3 feature extraction: RGB dense/global visual features
  |-- fusion/rerank: score normalization, NMS, parent-class policy
  v
standard provider response -> worker georeferences -> PostGIS + Neo4j
```

The service must follow the current provider contract:

```http
POST /detect
Content-Type: multipart/form-data

image=<chip.png>
metadata={
  "prompt_profile": "defense_eo_v1",
  "labels": ["aircraft", "ship"],
  "confidence_threshold": 0.15,
  "max_detections": 300,
  "geo": {
    "center_lat": 24.45,
    "center_lon": 54.38,
    "year": 2026,
    "day_of_year": 127
  },
  "source_bands": {
    "order": ["red", "green", "blue"],
    "hls_chip_path": null
  }
}
```

Response shape:

```json
{
  "status": "success",
  "task": "sam3_cdx_open_vocab_segmentation",
  "model": "facebook/sam3.1 + ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL + facebook/dinov3-vit7b16-pretrain-lvd1689m",
  "model_version": "sam3-cdx-pretrained-v1",
  "detections": [
    {
      "class": "aircraft",
      "original_class": "fixed-wing aircraft",
      "parent_class": "aircraft",
      "bbox": [0.52, 0.41, 0.08, 0.04],
      "obb": [0.48, 0.39, 0.56, 0.40, 0.56, 0.43, 0.48, 0.42],
      "confidence": 0.72,
      "sam3_score": 0.78,
      "prithvi_score": 0.67,
      "dinov3_score": 0.71,
      "area": 1382,
      "provider": "sam3-cdx"
    }
  ],
  "processing_time_ms": 1842.5,
  "prompt_profile": "defense_eo_v1",
  "labels_evaluated": 80,
  "device": "cuda:0"
}
```

### 2.2 Runtime Components

Create `inference-sam3-cdx/` with:

- `main.py`: FastAPI app, model lifecycle, `/detect`, `/health`.
- `serve.py`: copied/adapted from current inference services for worker/thread sizing.
- `labels.py`: label registry and prompt profiles.
- `preprocess.py`: image decoding, RGB chip normalization, optional EO six-band loading.
- `sam3_runner.py`: SAM3 model loading and prompt inference.
- `feature_extractors.py`: Prithvi and DINOv3 embedding helpers.
- `fusion.py`: score fusion, NMS, mask-to-OBB conversion, provider response formatting.
- `requirements.txt`: FastAPI, Torch, Transformers, TerraTorch, rasterio, OpenCV, SAM3 install dependencies.
- `Dockerfile.gpu`: CUDA image, installs PyTorch, SAM3 repo, TerraTorch, Transformers, and snapshots Hugging Face weights at build time.

Backend integration:

- Add `INFERENCE_SAM3_CDX_URL=http://inference-sam3-cdx:8001`.
- Add provider key `sam3-cdx` to `INFERENCE_PROVIDERS`, `_KNOWN_INFERENCE_PROVIDERS`, provider lifecycle, tests, and compose profile.
- Add `sam3-cdx` to `GROUNDED_PROVIDERS` only if it is configured to refine detections from other providers. By default it should run as a standalone prompted provider because SAM3 can produce boxes/masks from text prompts directly.
- Add `sam3-cdx_models` Docker volume for gated/cached weights.

## 3. Label Registry

### 3.1 Default Detectable Labels

The endpoint cannot enumerate "all possible SAM3 concepts" at runtime because SAM3 is promptable; the detectable set is the prompt vocabulary submitted to the model. The default profile should include the repo's existing LAE-style EO labels so `sam3-cdx` immediately covers the current geospatial use case.

Default `defense_eo_v1` labels:

```python
DEFENSE_EO_LABELS = (
    "airplane",
    "airport",
    "groundtrackfield",
    "harbor",
    "baseballfield",
    "overpass",
    "basketballcourt",
    "bridge",
    "stadium",
    "storagetank",
    "tenniscourt",
    "expressway service area",
    "trainstation",
    "expressway toll station",
    "vehicle",
    "golffield",
    "windmill",
    "dam",
    "helicopter",
    "roundabout",
    "soccer ball field",
    "swimming pool",
    "container crane",
    "helipad",
    "bus",
    "cargo truck",
    "dry cargo ship",
    "dump truck",
    "engineering ship",
    "excavator",
    "fishing boat",
    "intersection",
    "liquid cargo ship",
    "motorboat",
    "passenger ship",
    "small car",
    "tractor",
    "trailer",
    "truck tractor",
    "tugboat",
    "van",
    "warship",
    "working condensing tower",
    "unworking condensing tower",
    "working chimney",
    "unworking chimney",
    "fixed-wing aircraft",
    "small aircraft",
    "cargo plane",
    "pickup truck",
    "utility truck",
    "passenger car",
    "cargo car",
    "flat car",
    "locomotive",
    "sailboat",
    "barge",
    "ferry",
    "yacht",
    "oil tanker",
    "engineering vehicle",
    "tower crane",
    "reach stacker",
    "straddle carrier",
    "mobile crane",
    "haul truck",
    "front loader/bulldozer",
    "cement mixer",
    "ground grader",
    "hut/tent",
    "shed",
    "building",
    "aircraft hangar",
    "damaged building",
    "facility",
    "construction site",
    "shipping container lot",
    "shipping container",
    "pylon",
    "tower",
)
```

Parent-class mapping should reuse `backend/detection_policy.py` logic by keeping names compatible with current parent classes: `aircraft`, `ship`, `vehicle`, `military_vehicle`, `storage_tank`, `bridge`, `harbor`, `airfield`, `building`, `infrastructure`, and existing distractors.

### 3.2 Custom Labels

Support three prompt modes:

- `prompt_profile=defense_eo_v1`: use default labels above.
- `labels=[...]`: use caller-supplied labels for this request.
- `SAM3_CDX_LABEL_FILE=/app/vocab/custom_labels.json`: load a local offline label set with `{"labels": ["..."]}`.

Validation:

- Trim labels, lower-case only for normalization, preserve display text in `original_class`.
- Reject empty label sets with HTTP 400.
- Limit labels per request using `SAM3_CDX_MAX_LABELS`, default `80`, to avoid runaway SAM3 prompt loops.
- Chunk labels using `SAM3_CDX_PROMPT_BATCH_SIZE`, default `1` for SAM3 text prompts unless upstream batched prompt examples are verified stable in the local install.

## 4. High-Level Implementation Plan

### Phase 1 - Scaffold the New Provider

1. Create `inference-sam3-cdx/` by following the existing `inference-sam2` and `inference-lae-dino` structure.
2. Implement a health endpoint that reports:
   - SAM3 loaded/access error.
   - Prithvi active model ID (`600M-TL` or `300M` fallback).
   - DINOv3 model ID.
   - device, dtype, replicas, label profile, and offline/cache settings.
3. Implement `POST /detect` with decoded RGB PNG/JPEG support first.
4. Return empty detections with `status=success` when no prompt matches; return 503 only when models cannot load.

### Phase 2 - Model Loading

1. SAM3:
   - Prefer `facebook/sam3.1` checkpoints if access is available because upstream marks SAM 3.1 as the newer checkpoint set.
   - Allow `SAM3_MODEL_ID=facebook/sam3` fallback.
   - Require Hugging Face authentication during build or first online bootstrap; document `HF_TOKEN`/`hf auth login`.
   - Use `build_sam3_image_model()` and `Sam3Processor` per upstream API.
2. Prithvi:
   - Load `ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL` through TerraTorch `BACKBONE_REGISTRY`.
   - If `SAM3_CDX_PRITHVI_MODEL_ID=ibm-nasa-geospatial/Prithvi-EO-2.0-300M`, load the 300M fallback.
   - Use TL metadata when `center_lat`, `center_lon`, `year`, and `day_of_year` are provided; otherwise run metadata-drop-compatible inference.
3. DINOv3:
   - Load `AutoImageProcessor` and `AutoModel` from `facebook/dinov3-vit7b16-pretrain-lvd1689m`.
   - Use `device_map=auto` only if the service process is allowed to span devices; otherwise pin to the selected replica device.

### Phase 3 - Detection Pipeline

For each image chip:

1. Resolve label plan from metadata.
2. Run SAM3 once per label or per supported label batch.
3. Convert each SAM3 mask/box to normalized bbox, optional OBB, and area.
4. Crop/mask each candidate region.
5. Extract DINOv3 RGB embeddings from the crop and the full chip.
6. Extract Prithvi embeddings from EO bands when available; for RGB-only uploads, mark Prithvi as `not_available` instead of fabricating EO bands.
7. Compute a fused confidence:
   - SAM3 score remains the primary signal.
   - DINOv3 contributes a region-quality/rerank score derived from crop/full-image embedding consistency and optional duplicate clustering.
   - Prithvi contributes only when real HLS-like bands or a source GeoTIFF chip path is available.
8. Apply per-label threshold, max detections, and class-aware NMS.
9. Return standard provider detections.

### Phase 4 - Backend Wiring

1. Add `sam3-cdx` to provider parsing in `backend/main.py`.
2. Add `INFERENCE_SAM3_CDX_URL` and provider key in `backend/worker.py`.
3. Add compose service `inference-sam3-cdx` with profile `sam3-cdx`.
4. Add `.env.example` settings:
   - `INFERENCE_SAM3_CDX_URL`
   - `SAM3_CDX_DEVICE`
   - `SAM3_MODEL_ID`
   - `SAM3_CDX_PRITHVI_MODEL_ID`
   - `SAM3_CDX_DINOV3_MODEL_ID`
   - `SAM3_CDX_LABEL_PROFILE`
   - `SAM3_CDX_MAX_LABELS`
   - `SAM3_CDX_CONFIDENCE_THRESHOLD`
5. Update provider lifecycle if it enumerates compose services by provider name.
6. Add backend tests proving provider parsing accepts `sam3-cdx` and preserves order/deduping.

### Phase 5 - Validation and Ops

1. Unit-test label resolution, metadata parsing, score fusion, mask-to-OBB, and NMS without loading full models.
2. Add a smoke script that calls `/health` and one `/detect` request against a tiny local RGB chip.
3. Add build-time or bootstrap-time model snapshot logic, with runtime `TRANSFORMERS_OFFLINE=1` and `HF_HUB_OFFLINE=1` supported after weights are cached.
4. Document hardware expectations. SAM3 plus DINOv3 ViT-7B plus Prithvi-600M-TL is a large GPU workload; the service should support disabling DINO or using Prithvi 300M when memory is insufficient.

## 5. Low-Level Design and Pseudocode

### 5.1 Configuration

```python
SAM3_MODEL_ID = os.getenv("SAM3_MODEL_ID", "facebook/sam3.1")
PRITHVI_MODEL_ID = os.getenv(
    "SAM3_CDX_PRITHVI_MODEL_ID",
    "ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL",
)
PRITHVI_FALLBACK_MODEL_ID = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M"
DINOV3_MODEL_ID = os.getenv(
    "SAM3_CDX_DINOV3_MODEL_ID",
    "facebook/dinov3-vit7b16-pretrain-lvd1689m",
)
LABEL_PROFILE = os.getenv("SAM3_CDX_LABEL_PROFILE", "defense_eo_v1")
CONFIDENCE_THRESHOLD = float(os.getenv("SAM3_CDX_CONFIDENCE_THRESHOLD", "0.15"))
MAX_LABELS = int(os.getenv("SAM3_CDX_MAX_LABELS", "80"))
MAX_DETECTIONS = int(os.getenv("MAX_DETECTIONS_PER_CHIP", "300"))
ENABLE_PRITHVI = os.getenv("SAM3_CDX_ENABLE_PRITHVI", "1") == "1"
ENABLE_DINOV3 = os.getenv("SAM3_CDX_ENABLE_DINOV3", "1") == "1"
```

### 5.2 Model Bundle

```python
@dataclass
class ModelBundle:
    sam3_model: Any
    sam3_processor: Any
    prithvi_model: Any | None
    prithvi_model_id: str | None
    dinov3_processor: Any | None
    dinov3_model: Any | None
    device: str
    lock: threading.Lock


def load_bundle(device: str) -> ModelBundle:
    sam3_model = build_sam3_image_model(checkpoint_path_or_model_id=SAM3_MODEL_ID)
    sam3_processor = Sam3Processor(sam3_model)

    prithvi_model = None
    prithvi_model_id = None
    if ENABLE_PRITHVI:
        try:
            prithvi_model = BACKBONE_REGISTRY.build(PRITHVI_MODEL_ID, pretrained=True).to(device).eval()
            prithvi_model_id = PRITHVI_MODEL_ID
        except Exception:
            prithvi_model = BACKBONE_REGISTRY.build(PRITHVI_FALLBACK_MODEL_ID, pretrained=True).to(device).eval()
            prithvi_model_id = PRITHVI_FALLBACK_MODEL_ID

    dinov3_processor = dinov3_model = None
    if ENABLE_DINOV3:
        dinov3_processor = AutoImageProcessor.from_pretrained(DINOV3_MODEL_ID)
        dinov3_model = AutoModel.from_pretrained(DINOV3_MODEL_ID).to(device).eval()

    return ModelBundle(
        sam3_model=sam3_model,
        sam3_processor=sam3_processor,
        prithvi_model=prithvi_model,
        prithvi_model_id=prithvi_model_id,
        dinov3_processor=dinov3_processor,
        dinov3_model=dinov3_model,
        device=device,
        lock=threading.Lock(),
    )
```

Note: confirm the exact SAM3 checkpoint argument after installing the current upstream package; the README shows `build_sam3_image_model()` without explicit arguments, so the implementation should follow the installed API and keep the model ID/env handling near the loader.

### 5.3 Label Resolution

```python
def resolve_labels(metadata: dict) -> list[str]:
    if isinstance(metadata.get("labels"), list):
        labels = metadata["labels"]
    elif isinstance(metadata.get("text_prompt"), str):
        labels = [part.strip() for part in metadata["text_prompt"].split(".")]
    elif SAM3_CDX_LABEL_FILE:
        labels = load_label_file(SAM3_CDX_LABEL_FILE)
    else:
        labels = DEFENSE_EO_LABELS

    labels = dedupe_preserve_order(normalize_label_text(x) for x in labels)
    labels = [x for x in labels if x]
    if not labels:
        raise HTTPException(status_code=400, detail="No labels supplied for SAM3-CDX")
    return labels[:MAX_LABELS]
```

### 5.4 SAM3 Prompt Execution

```python
def run_sam3_for_labels(bundle: ModelBundle, image: PIL.Image.Image, labels: list[str]) -> list[Candidate]:
    candidates = []
    state = bundle.sam3_processor.set_image(image)

    for label in labels:
        output = bundle.sam3_processor.set_text_prompt(
            state=state,
            prompt=label,
        )
        masks = output.get("masks", [])
        boxes = output.get("boxes", [])
        scores = output.get("scores", [])

        for mask, box, score in zip(masks, boxes, scores):
            if float(score) < SAM3_MIN_RAW_SCORE:
                continue
            candidates.append(Candidate(
                label=label,
                mask=to_numpy(mask),
                box_xyxy=to_xyxy_pixels(box),
                sam3_score=float(score),
            ))

    return candidates
```

### 5.5 Feature Extraction

```python
def extract_dinov3_score(bundle: ModelBundle, image: PIL.Image.Image, candidate: Candidate) -> float | None:
    if bundle.dinov3_model is None:
        return None
    crop = crop_candidate(image, candidate.box_xyxy, candidate.mask)
    inputs = bundle.dinov3_processor(images=crop, return_tensors="pt").to(bundle.device)
    with torch.inference_mode():
        outputs = bundle.dinov3_model(**inputs)
    embedding = l2_normalize(outputs.pooler_output)
    return region_quality_score(embedding, candidate)


def extract_prithvi_score(bundle: ModelBundle, eo_tensor: torch.Tensor | None, geo_meta: dict, candidate: Candidate) -> float | None:
    if bundle.prithvi_model is None or eo_tensor is None:
        return None
    candidate_tensor = mask_or_crop_eo_tensor(eo_tensor, candidate.mask)
    kwargs = build_prithvi_kwargs(candidate_tensor, geo_meta)
    with torch.inference_mode():
        features = bundle.prithvi_model(**kwargs)
    return eo_region_quality_score(features, candidate)
```

Prithvi input rule:

- If the uploaded chip is only RGB PNG/JPEG, do not synthesize fake SWIR/NIR bands.
- If metadata provides a source HLS/GeoTIFF path or pre-extracted six-band tensor, use the documented HLS band order.
- If temporal/location metadata is absent, call the backbone without TL metadata if supported by TerraTorch; otherwise pass null/drop-compatible values as the local TerraTorch API expects.

### 5.6 Score Fusion

```python
def fuse_score(sam3_score: float, dinov3_score: float | None, prithvi_score: float | None) -> float:
    weights = {"sam3": 0.70, "dinov3": 0.15, "prithvi": 0.15}
    total = weights["sam3"] * sam3_score
    denom = weights["sam3"]

    if dinov3_score is not None:
        total += weights["dinov3"] * dinov3_score
        denom += weights["dinov3"]
    if prithvi_score is not None:
        total += weights["prithvi"] * prithvi_score
        denom += weights["prithvi"]

    return max(0.0, min(1.0, total / denom))
```

Initial weights should stay conservative because SAM3 is the only component with a direct prompt-to-mask confidence. DINOv3 and Prithvi scores are supporting signals unless a later evaluation justifies changing weights.

### 5.7 Candidate Formatting

```python
def candidate_to_detection(candidate: Candidate, img_w: int, img_h: int) -> dict:
    x1, y1, x2, y2 = candidate.box_xyxy
    bbox = [
        ((x1 + x2) / 2) / img_w,
        ((y1 + y2) / 2) / img_h,
        (x2 - x1) / img_w,
        (y2 - y1) / img_h,
    ]
    parent = parent_class_for_label(candidate.label)
    return {
        "class": parent,
        "original_class": candidate.label,
        "parent_class": parent,
        "bbox": clamp_bbox(bbox),
        "obb": mask_to_obb_normalized(candidate.mask, img_w, img_h),
        "confidence": candidate.fused_score,
        "sam3_score": candidate.sam3_score,
        "dinov3_score": candidate.dinov3_score,
        "prithvi_score": candidate.prithvi_score,
        "area": int(candidate.mask.sum()),
        "provider": "sam3-cdx",
        "task": "sam3_cdx_open_vocab_segmentation",
    }
```

### 5.8 Full Request Flow

```python
@app.post("/detect")
async def detect_objects(image: UploadFile = File(...), metadata: str = Form("{}")):
    meta = parse_json(metadata)
    contents = await image.read()
    pil_image, np_image = decode_rgb(contents)
    labels = resolve_labels(meta)
    bundle = next_model_bundle()

    with bundle.lock, torch.inference_mode():
        candidates = run_sam3_for_labels(bundle, pil_image, labels)
        eo_tensor = try_load_eo_tensor(meta)
        for candidate in candidates:
            candidate.dinov3_score = extract_dinov3_score(bundle, pil_image, candidate)
            candidate.prithvi_score = extract_prithvi_score(bundle, eo_tensor, meta.get("geo", {}), candidate)
            candidate.fused_score = fuse_score(
                candidate.sam3_score,
                candidate.dinov3_score,
                candidate.prithvi_score,
            )

    detections = [
        candidate_to_detection(c, pil_image.width, pil_image.height)
        for c in candidates
        if c.fused_score >= threshold_for_label(c.label, meta)
    ]
    detections = class_aware_mask_nms(detections, iou_threshold=SAM3_CDX_NMS_IOU)
    detections = sorted(detections, key=lambda d: d["confidence"], reverse=True)[:MAX_DETECTIONS]

    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": elapsed_ms(),
        "task": "sam3_cdx_open_vocab_segmentation",
        "model": model_summary(bundle),
        "model_version": "sam3-cdx-pretrained-v1",
        "prompt_profile": meta.get("prompt_profile", LABEL_PROFILE),
        "labels_evaluated": len(labels),
        "device": bundle.device,
        "input_metadata": meta,
    }
```

## 6. Docker and Compose Plan

### 6.1 Dockerfile Strategy

`inference-sam3-cdx/Dockerfile.gpu` should be based on the existing CUDA inference Dockerfiles:

```dockerfile
ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-devel-ubuntu22.04

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
ARG TORCH_VERSION=2.10.0
ARG TORCHVISION_VERSION=0.25.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git python3 python3-pip libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip setuptools wheel
RUN pip install "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
    --index-url ${TORCH_INDEX_URL} --extra-index-url https://pypi.org/simple

RUN git clone https://github.com/facebookresearch/sam3.git /opt/sam3 \
    && cd /opt/sam3 \
    && pip install -e .

RUN pip install terratorch "transformers>=4.42,<5" huggingface_hub accelerate rasterio opencv-python-headless

# Optional online build snapshot. For gated SAM3/DINOv3 access, this requires HF_TOKEN.
ARG HF_TOKEN
ARG SAM3_MODEL_ID=facebook/sam3.1
ARG PRITHVI_MODEL_ID=ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL
ARG DINOV3_MODEL_ID=facebook/dinov3-vit7b16-pretrain-lvd1689m

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . /app

CMD ["python", "serve.py"]
```

Use PyTorch version/build args generated by `scripts/configure_host.py` once the host GPU profile is known. SAM3 upstream currently lists Python 3.12, PyTorch 2.7+, and CUDA 12.6+ prerequisites; the final Dockerfile should align with the repo's install constraints after a local build test.

### 6.2 Compose Service

```yaml
inference-sam3-cdx:
  profiles: ["sam3-cdx", "all"]
  build:
    context: ./inference-sam3-cdx
    dockerfile: Dockerfile.gpu
    args:
      CUDA_VERSION: ${SAM3_CDX_CUDA_VERSION:?Run python scripts/configure_host.py first}
      TORCH_INDEX_URL: ${SAM3_CDX_TORCH_INDEX_URL:?Run python scripts/configure_host.py first}
      TORCH_VERSION: ${SAM3_CDX_TORCH_VERSION:?Run python scripts/configure_host.py first}
      TORCHVISION_VERSION: ${SAM3_CDX_TORCHVISION_VERSION:?Run python scripts/configure_host.py first}
      HF_TOKEN: ${HF_TOKEN:-}
  image: sentinelos-inference-sam3-cdx:gpu
  gpus: all
  environment:
    GPU_MODEL: ${GPU_MODEL:?Run python scripts/configure_host.py first}
    SAM3_CDX_GPU_PROFILE: ${SAM3_CDX_GPU_PROFILE:?Run python scripts/configure_host.py first}
    NVIDIA_VISIBLE_DEVICES: ${NVIDIA_VISIBLE_DEVICES:-all}
    NVIDIA_DRIVER_CAPABILITIES: ${NVIDIA_DRIVER_CAPABILITIES:-compute,utility}
    DEVICE: ${SAM3_CDX_DEVICE:-auto}
    CPU_THREADS: auto
    WEB_CONCURRENCY: "1"
    SAM3_MODEL_ID: ${SAM3_MODEL_ID:-facebook/sam3.1}
    SAM3_CDX_PRITHVI_MODEL_ID: ${SAM3_CDX_PRITHVI_MODEL_ID:-ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL}
    SAM3_CDX_DINOV3_MODEL_ID: ${SAM3_CDX_DINOV3_MODEL_ID:-facebook/dinov3-vit7b16-pretrain-lvd1689m}
    SAM3_CDX_LABEL_PROFILE: defense_eo_v1
    SAM3_CDX_CONFIDENCE_THRESHOLD: "0.15"
    MAX_DETECTIONS_PER_CHIP: "300"
    TRANSFORMERS_OFFLINE: ${TRANSFORMERS_OFFLINE:-1}
    HF_HUB_OFFLINE: ${HF_HUB_OFFLINE:-1}
  volumes:
    - ./inference-sam3-cdx:/app
    - sam3_cdx_models:/models
    - imagery_data:/data/imagery:ro
  working_dir: /app
  healthcheck:
    test: ["CMD-SHELL", "python3 -c 'import socket; s = socket.socket(); s.settimeout(2); exit(0 if s.connect_ex((\"127.0.0.1\", 8001)) == 0 else 1)' || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 30s
```

Add volume:

```yaml
volumes:
  sam3_cdx_models:
```

## 7. Backend Integration Details

### 7.1 Provider Registration

In `backend/worker.py`:

```python
INFERENCE_SAM3_CDX_URL = os.getenv("INFERENCE_SAM3_CDX_URL", "http://inference-sam3-cdx:8001")

INFERENCE_PROVIDERS = {
    "yolo": INFERENCE_URL,
    "lae-dino": INFERENCE_LAE_DINO_URL,
    "mmrotate": INFERENCE_MMROTATE_URL,
    "lsknet": INFERENCE_LSKNET_URL,
    "sam2": INFERENCE_SAM2_URL,
    "sam3-cdx": INFERENCE_SAM3_CDX_URL,
}
```

In `backend/main.py`:

```python
_KNOWN_INFERENCE_PROVIDERS = ("yolo", "lae-dino", "mmrotate", "lsknet", "sam2", "sam3-cdx")
```

### 7.2 Grounded-Provider Policy

Default:

```python
GROUNDED_PROVIDERS: set[str] = {"sam2"}
```

Do not add `sam3-cdx` to `GROUNDED_PROVIDERS` initially because SAM3 can run text-prompted detection directly from the label registry. Add a later mode only if analysts want `sam3-cdx` to refine YOLO/MMRotate boxes. If enabled later, `sam3-cdx` should interpret `metadata.prompt_boxes` as region constraints and run SAM3 with the prompt label attached to each box.

### 7.3 Upload UX/API

No new backend upload endpoint is required. Existing `inference_providers` form field can accept:

```text
sam3-cdx
yolo,sam3-cdx
lae-dino,sam3-cdx
yolo,mmrotate,lsknet,sam3-cdx
```

Frontend provider selectors should add a checkbox label:

```text
SAM3-CDX (SAM3 + Prithvi + DINOv3, pretrained)
```

## 8. Testing Plan

### 8.1 Unit Tests

Add tests:

- `inference-sam3-cdx/tests/test_labels.py`
  - default label registry returns all `defense_eo_v1` labels.
  - request labels override default profile.
  - empty labels produce validation error.
  - max-label cap is enforced.
- `inference-sam3-cdx/tests/test_fusion.py`
  - SAM3-only score returns SAM3 score.
  - DINO/Prithvi missing values do not lower score by denominator error.
  - all fused scores are clamped to `[0, 1]`.
- `inference-sam3-cdx/tests/test_geometry.py`
  - mask-to-OBB returns eight normalized values.
  - bbox normalization clamps out-of-bounds boxes.
  - class-aware NMS keeps different labels even when boxes overlap.
- `backend/tests/test_inference_providers.py`
  - provider parser accepts `sam3-cdx`.
  - duplicates are removed while preserving order.
  - unknown-only provider input still falls back to `yolo`.

### 8.2 Smoke Tests

Add `inference-sam3-cdx/smoke_sam3_cdx_service.py`:

```python
def main():
    resp = requests.get("http://localhost:8001/health", timeout=5)
    assert resp.status_code == 200

    with open("probes/probe_chip.png", "rb") as handle:
        detect = requests.post(
            "http://localhost:8001/detect",
            files={"image": ("probe_chip.png", handle, "image/png")},
            data={"metadata": json.dumps({"labels": ["airplane", "ship", "vehicle"]})},
            timeout=120,
        )
    detect.raise_for_status()
    body = detect.json()
    assert body["status"] == "success"
    assert "detections" in body
```

### 8.3 Integration Tests

Run:

```powershell
docker compose --profile sam3-cdx up --build inference-sam3-cdx
docker compose --profile sam3-cdx --profile all up backend worker
pytest -q backend/tests/test_inference_providers.py
```

Manual acceptance:

1. Upload a small optical raster with `inference_providers=sam3-cdx`.
2. Confirm worker progress reaches detection storage.
3. Confirm `/api/detections/classes` includes labels from `sam3-cdx`.
4. Confirm `/api/detections/geojson` includes metadata fields `providers=["sam3-cdx"]`, `sam3_score`, `dinov3_score`, and `prithvi_score` when available.

## 9. Operational Constraints and Defaults

- Pretrained only: no training jobs, no fine-tuning, no adapter heads, no local classifier fitting.
- SAM3 checkpoints are gated; deployment must include a Hugging Face access workflow and cache/volume strategy.
- Runtime should support offline mode after weights are cached. Build-time model downloads may require network access unless an offline bundle is prepared.
- DINOv3 ViT-7B is large. If GPU memory is insufficient, set `SAM3_CDX_ENABLE_DINOV3=0` or use a smaller DINOv3 model only if the user explicitly approves changing the requested model.
- Prithvi-600M-TL is the default. If it cannot load due to memory or dependency limits, the endpoint may fall back to the user-provided `Prithvi-EO-2.0-300M`, and `/health` must clearly report the active Prithvi model.
- Prithvi scoring is only meaningful for real EO multispectral inputs. For RGB-only chips, return `prithvi_score=null` and do not fabricate missing bands.
- Existing detection policy may suppress some labels as distractors. That behavior should remain consistent with `backend/detection_policy.py` unless the user separately requests taxonomy changes.

## 10. Acceptance Criteria

- New `inference-sam3-cdx` service builds and exposes `/health` and `/detect`.
- `/detect` uses SAM3 text prompts from the default or requested label set and returns normalized detections without model training.
- DINOv3 and Prithvi are loaded from the requested pretrained model IDs when enabled; active/fallback model IDs are visible in health and responses.
- Backend upload accepts `inference_providers=sam3-cdx` and stores detections through the existing pipeline.
- Multi-provider runs such as `yolo,sam3-cdx` do not break dedupe, confirmation policy, or detection storage.
- Test coverage includes provider parsing, label resolution, fusion math, geometry conversion, and a service smoke test.
- Documentation states the model limitations clearly: SAM3 provides promptable detection/segmentation; Prithvi and DINOv3 provide pretrained features/reranking, not unconstrained label classification.
