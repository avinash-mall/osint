# SAM3 + Prithvi-EO-2.0 + DINOv3 Inference Endpoint — Plan

## Context

The OSINT system at `d:\osint` already runs five FastAPI inference services (`yolo`, `lae-dino`, `mmrotate`, `lsknet`, `sam2`), all on internal port 8001 and conforming to a common `/detect` contract. The Celery worker tiles imagery (`backend/worker.py`) and dispatches chips to providers; results are reconciled by spatial-IoU consensus and stored in PostGIS.

The user wants a sixth service, **`inference-sam3`**, that bundles three pretrained models — **no training, no fine-tuning, weights only** — to expand the labels the system can detect:

- **SAM3** (`facebook/sam3.1`) — promptable open-vocabulary concept segmentation. The text prompt IS the label; SAM3 returns `(masks, boxes, scores)` for each concept. This is the primary labeller.
- **Prithvi-EO-2.0** — used through IBM/NASA's **published pretrained downstream models** (no fine-tuning by us):
  - `ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11` — flood/water segmentation
  - `ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars` — burn scar segmentation
  - `ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification` — crop classes
  - Backbone-only `Prithvi-EO-2.0-600M-TL` and `300M` are loaded for embedding extraction
- **DINOv3** (`facebook/dinov3-vitl16-pretrain-lvd1689m` default; `dinov3-vit7b16` opt-in) — vision-only foundation backbone. **Cannot label** (no classifier head ships, no text encoder). Used as a frozen embedder: emits a dense feature vector per detected region for storage, dedup, and future similarity search.

**Hard constraints**
- No model training or fine-tuning. Only published pretrained weights.
- No locally-built prototype banks (user explicit answer).
- All weights downloaded from HF at image build time and baked in (offline at runtime).
- Match the existing inference-service contract so the backend integrates with minimal changes.

**Outcome**
- A new endpoint that produces SAM3 segmentations labelled by their text prompts, plus complementary Prithvi-derived labels (flood, burn scar, crop class) for satellite/multispectral chips, plus a DINOv3 embedding attached to every detection.
- Existing taxonomy ([training_dataset/yolo/taxonomy.json](../training_dataset/yolo/taxonomy.json) — 10 enabled defense parents) is the default SAM3 prompt set; the request can override with arbitrary open-vocab prompts.

---

## Architecture

### Service topology

```
                       ┌──────────────────────────────────────┐
                       │  backend/worker.py (Celery)          │
                       │  slice_and_infer() ──> chip dispatch │
                       └────────┬─────────────────────────────┘
                                │ HTTP multipart/form-data
        ┌──────────────┬────────┼──────────────┬──────────────┬────────────────────┐
        ▼              ▼        ▼              ▼              ▼                    ▼
   inference        inference-  inference-  inference-   inference-   inference-sam3 ★ NEW
   (YOLO :8001)     lae-dino    mmrotate    lsknet       sam2         ────────────────
                                                                       FastAPI :8001
                                                                       │
        ┌──────────────────────────────────────┬───────────────────────┴───────────────┐
        ▼                                      ▼                                       ▼
  SAM3 (mask + score, label=prompt)     DINOv3 (frozen embedder,            Prithvi heads
  facebook/sam3.1                        no labelling — vector only)        (downloaded pretrained)
                                                                            ├ Sen1Floods11
                                                                            ├ BurnScars
                                                                            └ crop classification
                                                                            (multispectral path only)
```

### Per-chip pipeline

```
  request: image (RGB or HLS-6 GeoTIFF) +
           metadata{ modality, text_prompts?, prompt_boxes?, prithvi_tasks? }
                      │
                      ▼
        ┌──── choose mode ────┐
        │                      │
        ▼                      ▼
  Mode A: text-driven         Mode B: box-prompted
  (default)                   (consensus phase, like SAM2)
  for each prompt p:           SAM3 receives prompt_boxes,
    SAM3.set_text_prompt(p)    returns masks for each box;
    -> masks, boxes, scores    label inherited from caller
        │                      │
        └──────────┬───────────┘
                   ▼
        candidates = [(mask, box, score, label)]
                   ▼
       ┌─── per-detection enrichment ───┐
       │                                  │
       ▼                                  ▼
   DINOv3 embed                      Prithvi (multispectral only)
   crop = chip[bbox]                 run pretrained heads on whole chip
   emb = dinov3.cls_token(crop)      flood_mask, burn_mask, crop_label_map
   attach emb to detection           overlay → if mask intersects detection
                                     bbox > THRESH, append "flood"/"burnscar"/
                                     "crop:<class>" to detection.labels[]
                   │
                   ▼
   filter:  per-class confidence threshold, mask-aware NMS,
            valid-mask clip (worker.py:clip_box_to_valid_mask pattern)
                   │
                   ▼
   normalize → standard detection contract
```

### Where the labels come from (no training)

| Source | Label space | How |
|---|---|---|
| **SAM3 text prompts** | Default = 10 OSINT defense parents (`aircraft, ship, vehicle, military_vehicle, storage_tank, bridge, harbor, airfield, building, infrastructure`). Caller may override with arbitrary text. | Prompt-driven; SAM3 returns instances matching the prompt. |
| **Prithvi-Sen1Floods11** | `flood`, `water` | Pretrained binary segmenter; runs on full HLS-6 chip; intersected with SAM3 boxes. |
| **Prithvi-BurnScars** | `burn_scar` | Pretrained binary segmenter; same. |
| **Prithvi-multi-temporal-crop** | crop class set published by IBM (e.g., natural vegetation, forest, corn, soybeans, wetlands, …) | Pretrained classifier; intersected with SAM3 boxes. |
| **DINOv3** | (no labels) | Embedding-only. Stored on each detection for downstream use. |

### Storage / persistence (small additions)

- `detections.metadata` (existing JSONB) gets `mask_rle`, `dinov3_embedding` (base64-packed float16, ~2 KB for ViT-L), `prithvi_labels` array.
- No DB schema change strictly required — JSONB absorbs it. (A future `pgvector` migration is mentioned but out of scope.)

---

## High-Level Plan

1. **Bootstrap service skeleton** by copying [inference-sam2/](../inference-sam2/) to `inference-sam3/`. Keep the same FastAPI app, model-pool pattern ([inference-sam2/main.py:56-216](../inference-sam2/main.py#L56-L216)), `decode_image` helper ([inference-sam2/main.py:465-470](../inference-sam2/main.py#L465-L470)), `/detect` + `/health` shape ([inference-sam2/main.py:472-517](../inference-sam2/main.py#L472-L517)), port 8001.
2. **Wire SAM3** — `pip install git+https://github.com/facebookresearch/sam3@main`. Load `facebook/sam3.1` weights. Auth via build-time `HF_TOKEN` (gated repo). Lazy-load on first `/detect`, replicate per GPU.
3. **Wire DINOv3** — `transformers.AutoModel.from_pretrained(DINOV3_MODEL_ID, torch_dtype=fp16, device_map="auto")`. `DINOV3_MODEL_ID` env var; **default `facebook/dinov3-vitl16-pretrain-lvd1689m`** (~600 MB FP16); switchable to `dinov3-vit7b16-pretrain-lvd1689m` (gated, ~14 GB). Used as embedder only — no classifier head.
4. **Wire Prithvi backbone + downstream heads**:
   - Backbone: `terratorch.BACKBONE_REGISTRY.build("ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL", pretrained=True)` (or 300M).
   - Downstream pretrained heads (HF, no training): `Prithvi-EO-2.0-300M-TL-Sen1Floods11`, `Prithvi-EO-2.0-300M-BurnScars`, `Prithvi-EO-1.0-100M-multi-temporal-crop-classification`. Each is a TerraTorch task module that loads its own weights.
5. **Multispectral preprocessing** — new `inference-sam3/multispectral.py`. Decodes 6-band HLS GeoTIFF (B,G,R,Narrow-NIR,SWIR1,SWIR2), applies Prithvi mean/std, plus a `hls_to_rgb_preview()` for SAM3/DINOv3 (which want RGB). Mirrors [backend/worker.py:350-359 chip_to_uint8_rgb](../backend/worker.py#L350-L359) semantics.
6. **Backend multispectral chip path** — [backend/worker.py](../backend/worker.py) currently emits 3-band RGB chips. Add a `modality=multispectral` branch that emits 6-band GeoTIFF chips when SAM3 is in the provider list and the source raster has the required HLS bands.
7. **Service request handler** — `inference-sam3/main.py` implements Mode A (text prompts → SAM3 → label-by-prompt → DINOv3 embed → optional Prithvi overlay) and Mode B (`prompt_boxes` → SAM3 box-prompted → label inherited; same enrichment).
8. **Standard contract output** — same shape as SAM2 plus `mask_rle`, `dinov3_embedding`, optional `prithvi_labels`. Returns the parent_class (mapped via `taxonomy.json`) so backend consensus and `detection_policy.py` keep working.
9. **Docker + compose** — new `inference-sam3/Dockerfile.gpu`, new compose service on profile `["sam3", "all"]`, mapped to external port 8008. Add `INFERENCE_SAM3_URL=http://inference-sam3:8001` to `backend` and `worker`.
10. **Backend integration** — register provider in [backend/worker.py:32-44](../backend/worker.py#L32-L44). Extend the existing two-phase consensus dispatch (currently SAM2-only) to optionally route to SAM3 instead. Add SAM3 to `CONSENSUS_EXEMPT_PROVIDERS`. Add SAM3 thresholds in [backend/detection_policy.py:68-122](../backend/detection_policy.py#L68-L122).
11. **Smoke tests** — `inference-sam3/tests/`: `test_health.py`, `test_text_prompt.py`, `test_box_prompt.py`, `test_multispectral.py`. Same style as [inference-sam2/tests/](../inference-sam2/tests/).
12. **Documentation** — README section on SAM3 endpoint; `.env.example` for `HF_TOKEN`, model-id env vars, GPU profile.

---

## Low-Level Plan (with pseudocode)

### File layout

```
inference-sam3/
├── Dockerfile.gpu
├── requirements.txt
├── main.py                    # FastAPI app, /detect, /health, model pool
├── multispectral.py           # HLS-6 decoding + RGB preview
├── prompts.py                 # taxonomy → SAM3 text prompts
├── prithvi_heads.py           # wrappers for the 3 pretrained downstream heads
├── embedding.py               # DINOv3 pooled-feature extractor
├── probes/probe_chip.png
└── tests/
    ├── test_health.py
    ├── test_text_prompt.py
    ├── test_box_prompt.py
    └── test_multispectral.py
backend/                       # small edits
├── worker.py                  # add provider + multispectral chip emit
└── detection_policy.py        # add sam3 thresholds
docker-compose.yml             # add inference-sam3 service + sam3_models volume
README.md                      # document new endpoint
```

### `main.py` — request handler

```python
# Mirrors inference-sam2/main.py contract; key differences below.

DEFAULT_PROMPTS = prompts.taxonomy_text_prompts("optical-defense-v1")  # 10 parents
PROMPT_TEMPLATE  = 'a satellite image of a {label}'                    # tunable per class
TEXT_THRESHOLD   = float(os.getenv("SAM3_TEXT_THRESHOLD", "0.30"))
BOX_THRESHOLD    = float(os.getenv("SAM3_BOX_THRESHOLD",  "0.25"))

@app.post("/detect")
async def detect(image: UploadFile = File(...), metadata: str = Form("{}")):
    meta = json.loads(metadata)
    modality      = meta.get("modality", "rgb")            # "rgb" | "multispectral"
    text_prompts  = meta.get("text_prompts")               # optional override
    prompt_boxes  = meta.get("prompt_boxes")               # optional, SAM2-compatible
    prithvi_tasks = meta.get("prithvi_tasks", ["flood", "burnscar", "crop"])

    # ---- decode chip ----
    raw = await image.read()
    if modality == "multispectral":
        chip6  = multispectral.decode_hls6(raw)            # (6,H,W) float32, normalized
        chip3  = multispectral.hls_to_rgb_preview(chip6)   # (H,W,3) uint8 — for SAM3 + DINOv3
    else:
        chip3  = decode_image(raw)                         # reuse from SAM2
        chip6  = None

    sam3, dinov3, prithvi = pool.next()                    # round-robin per-GPU pool

    # ---- generate candidates ----
    candidates = []                                        # list of (mask, bbox, score, label)
    if prompt_boxes:                                       # Mode B
        masks, scores = sam3_box_prompt(sam3, chip3, prompt_boxes)
        for box, m, s, lbl in zip(prompt_boxes, masks, scores, meta.get("prompt_labels", [None]*len(prompt_boxes))):
            if s >= BOX_THRESHOLD:
                candidates.append((m, box, s, lbl))        # label inherited from caller
    else:                                                  # Mode A — text-driven
        labels_to_use = text_prompts or DEFAULT_PROMPTS
        for label in labels_to_use:
            prompt = PROMPT_TEMPLATE.format(label=label)
            masks, boxes, scores = sam3_text_prompt(sam3, chip3, prompt)
            for m, b, s in zip(masks, boxes, scores):
                if s >= TEXT_THRESHOLD:
                    candidates.append((m, b, s, label))

    # ---- Prithvi overlays (multispectral only) ----
    overlays = {}
    if chip6 is not None and prithvi_tasks:
        if "flood" in prithvi_tasks:
            overlays["flood"] = prithvi_heads.run_flood(prithvi, chip6)        # binary mask (H,W)
        if "burnscar" in prithvi_tasks:
            overlays["burn_scar"] = prithvi_heads.run_burnscars(prithvi, chip6)
        if "crop" in prithvi_tasks:
            overlays["crop"] = prithvi_heads.run_crop_classification(prithvi, chip6)  # int (H,W)

    # ---- per-detection enrichment ----
    detections = []
    for mask, bbox, score, label in candidates:
        crop_rgb = crop_region(chip3, bbox)
        emb_vec = embedding.dinov3_pool(dinov3, crop_rgb)         # (D,) float16

        prithvi_labels = []
        for name, layer in overlays.items():
            if mask_overlap(mask, layer, bbox) > 0.30:
                prithvi_labels.append(name if name != "crop"
                                       else f"crop:{prithvi_heads.crop_class_name(layer, bbox)}")

        parent = TAXONOMY_PARENT.get(label, "unknown")
        detections.append({
            "class": parent,
            "original_class": label,
            "parent_class": parent,
            "bbox": normalize_bbox(bbox, chip3.shape),            # [cx,cy,w,h] in [0,1]
            "confidence": float(score),
            "mask_rle": encode_rle(mask),
            "dinov3_embedding": pack_fp16_b64(emb_vec),
            "prithvi_labels": prithvi_labels,
            "embedding_modality": "rgb" if chip6 is None else "multispectral",
            "policy_review_status": "review_candidate",
        })

    detections = mask_aware_nms(detections, iou=0.5)
    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": ...,
        "model_version": MODEL_VERSION,
        "modality": modality,
    }
```

### `multispectral.py`

```python
HLS_BAND_ORDER = ["B02","B03","B04","B8A","B11","B12"]   # Prithvi-EO-2.0 input
PRITHVI_MEAN   = np.array([...], dtype=np.float32)        # from HF model card
PRITHVI_STD    = np.array([...], dtype=np.float32)

def decode_hls6(payload: bytes) -> np.ndarray:
    """Decode a 6-band GeoTIFF chip → (6,H,W) float32, normalized to model space."""
    with rasterio.open(io.BytesIO(payload)) as src:
        if src.count < 6:
            raise HTTPException(400, f"Expected 6 HLS bands, got {src.count}")
        arr = src.read(indexes=range(1, 7)).astype(np.float32)
    arr = (arr - PRITHVI_MEAN[:, None, None]) / PRITHVI_STD[:, None, None]
    return arr

def hls_to_rgb_preview(arr_norm: np.ndarray) -> np.ndarray:
    """Map (6,H,W) Prithvi-normalized stack to a uint8 RGB chip for SAM3/DINOv3.
    Uses bands 2,1,0 (R,G,B) with 2–98 percentile stretch — same convention as
    backend/worker.py:chip_to_uint8_rgb."""
    rgb = arr_norm[[2, 1, 0]]                            # R,G,B
    p2, p98 = np.percentile(rgb, [2, 98], axis=(1, 2), keepdims=True)
    rgb = np.clip((rgb - p2) / np.maximum(p98 - p2, 1e-6), 0, 1)
    return (rgb * 255).astype(np.uint8).transpose(1, 2, 0)
```

### `prithvi_heads.py`

```python
"""Wraps the three published TerraTorch downstream models.
Loaded once at startup; weights baked in at image build time."""

import terratorch.tasks   # provides SemanticSegmentationTask, ClassificationTask, etc.

def load_flood_head():
    return terratorch.tasks.SemanticSegmentationTask.load_from_checkpoint(
        "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11")

def load_burnscars_head():
    return terratorch.tasks.SemanticSegmentationTask.load_from_checkpoint(
        "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars")

def load_crop_head():
    return terratorch.tasks.ClassificationTask.load_from_checkpoint(
        "ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification")

def run_flood(p, chip6):
    with torch.inference_mode():
        logits = p["flood"](torch.from_numpy(chip6).unsqueeze(0).cuda())
    return (logits.argmax(1).squeeze(0).cpu().numpy() == 1)   # boolean (H,W)

def run_burnscars(p, chip6):
    with torch.inference_mode():
        logits = p["burnscars"](torch.from_numpy(chip6).unsqueeze(0).cuda())
    return (logits.argmax(1).squeeze(0).cpu().numpy() == 1)

def run_crop_classification(p, chip6):
    with torch.inference_mode():
        logits = p["crop"](torch.from_numpy(chip6).unsqueeze(0).cuda())   # (1,K,H,W) or (1,K)
    return logits.argmax(1).squeeze(0).cpu().numpy()                       # (H,W) int

def crop_class_name(label_map, bbox):
    x1,y1,x2,y2 = bbox
    region = label_map[y1:y2, x1:x2]
    cls_id = int(np.bincount(region.flatten()).argmax())
    return CROP_CLASS_NAMES[cls_id]                       # from HF config.json
```

### `embedding.py`

```python
def dinov3_pool(model, crop_rgb_uint8):
    """Returns (D,) fp16 vector. D=1024 for ViT-L, 4096 for ViT-7B."""
    proc = transformers.AutoImageProcessor.from_pretrained(DINOV3_MODEL_ID)
    inp = proc(images=Image.fromarray(crop_rgb_uint8), return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out = model(**inp)
    cls = out.last_hidden_state[:, 0, :]                   # CLS token only
    return cls.squeeze(0).to(torch.float16).cpu().numpy()
```

### `prompts.py`

```python
TAXONOMY_PARENT = {
    "aircraft": "aircraft", "ship": "ship", "vehicle": "vehicle",
    "military_vehicle": "military_vehicle", "storage_tank": "storage_tank",
    "bridge": "bridge", "harbor": "harbor", "airfield": "airfield",
    "building": "building", "infrastructure": "infrastructure",
}

# Default open-vocab prompt phrasings — tuned per class for SAM3.
TAXONOMY_PROMPTS = {
    "aircraft":          "an airplane",
    "ship":              "a ship or boat",
    "vehicle":           "a car, truck, or land vehicle",
    "military_vehicle":  "a military vehicle, tank, or armored carrier",
    "storage_tank":      "a cylindrical industrial storage tank",
    "bridge":            "a bridge spanning water or land",
    "harbor":            "a harbor with docked ships",
    "airfield":          "an airfield runway",
    "building":          "a building",
    "infrastructure":    "infrastructure such as power lines or pipelines",
}

def taxonomy_text_prompts(taxonomy_version: str) -> list[str]:
    return [TAXONOMY_PROMPTS[c] for c in TAXONOMY_PARENT]
```

### `Dockerfile.gpu` (sketch)

```dockerfile
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-runtime-ubuntu22.04
ARG TORCH_INDEX_URL TORCH_VERSION TORCHVISION_VERSION HF_TOKEN
ARG DINOV3_MODEL_ID=facebook/dinov3-vitl16-pretrain-lvd1689m
ARG PRITHVI_BACKBONE_ID=ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL

RUN pip install --extra-index-url ${TORCH_INDEX_URL} \
        torch==${TORCH_VERSION} torchvision==${TORCHVISION_VERSION}
RUN pip install fastapi uvicorn rasterio pillow opencv-python-headless \
        transformers terratorch huggingface_hub \
        git+https://github.com/facebookresearch/sam3@main

# Pre-download all weights (gated repos require HF_TOKEN)
RUN python - <<'PY'
import os, huggingface_hub as hh
hh.login(token=os.environ['HF_TOKEN'])
from transformers import AutoModel
AutoModel.from_pretrained(os.environ['DINOV3_MODEL_ID'])
from sam3.model_builder import build_sam3_image_model; build_sam3_image_model()
import terratorch
terratorch.BACKBONE_REGISTRY.build(os.environ['PRITHVI_BACKBONE_ID'], pretrained=True)
for repo in [
    "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11",
    "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars",
    "ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification",
]:
    hh.snapshot_download(repo)
PY

COPY . /app
WORKDIR /app
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8001"]
```

### `docker-compose.yml` additions

```yaml
inference-sam3:
  profiles: ["sam3", "all"]
  build:
    context: ./inference-sam3
    dockerfile: Dockerfile.gpu
    args:
      CUDA_VERSION: ${SAM3_CUDA_VERSION:?Run scripts/configure_host.py first}
      TORCH_VERSION: ${SAM3_TORCH_VERSION:?...}
      TORCHVISION_VERSION: ${SAM3_TORCHVISION_VERSION:?...}
      HF_TOKEN: ${HF_TOKEN:?Set HF_TOKEN — sam3.1 and dinov3 are gated repos}
      DINOV3_MODEL_ID: ${DINOV3_MODEL_ID:-facebook/dinov3-vitl16-pretrain-lvd1689m}
      PRITHVI_BACKBONE_ID: ${PRITHVI_BACKBONE_ID:-ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL}
  image: sentinelos-inference-sam3:gpu
  gpus: all
  environment:
    DEVICE: ${SAM3_DEVICE:-auto}
    SAM3_MODEL_ID: facebook/sam3.1
    DINOV3_MODEL_ID: ${DINOV3_MODEL_ID:-facebook/dinov3-vitl16-pretrain-lvd1689m}
    PRITHVI_BACKBONE_ID: ${PRITHVI_BACKBONE_ID:-ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL}
    SAM3_TEXT_THRESHOLD: "0.30"
    SAM3_BOX_THRESHOLD:  "0.25"
    DETECTION_THRESHOLD_PROFILE: recall_review
    MODEL_VERSION: sam3-dinov3l-prithvi-v1
  volumes:
    - ./inference-sam3:/app
    - sam3_models:/models
    - imagery_data:/data/imagery:ro
  working_dir: /app
  healthcheck:
    test: ["CMD-SHELL", "python3 -c 'import socket; s=socket.socket(); s.settimeout(2); exit(0 if s.connect_ex((\"127.0.0.1\",8001))==0 else 1)' || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 30s

# in `volumes:` block, add:
sam3_models:
```

Add to `backend.environment` and `worker.environment`:
```
- INFERENCE_SAM3_URL=http://inference-sam3:8001
```

### `backend/worker.py` edits (lines 32–44)

```python
INFERENCE_PROVIDERS = {
    "yolo":      "http://localhost:8001",
    "lae-dino":  "http://inference-lae-dino:8001",
    "mmrotate":  "http://inference-mmrotate:8001",
    "lsknet":    "http://inference-lsknet:8001",
    "sam2":      "http://inference-sam2:8001",
    "sam3":      "http://inference-sam3:8001",      # NEW
}

GROUNDED_SEGMENTERS         = {"sam2", "sam3"}        # extended
CONSENSUS_EXEMPT_PROVIDERS  = {"sam2", "sam3"}        # extended
```

Multispectral chip-emission branch (sketch — same function emits 6-band GeoTIFF when applicable):

```python
def emit_chip_payload(window, src, providers):
    needs_multispectral = "sam3" in providers and src.count >= 6
    if needs_multispectral:
        return geotiff_window_bytes(src, window, indexes=HLS_INDEXES, dtype="float32"), \
               {"modality": "multispectral", "content_type": "image/tiff"}
    rgb = chip_to_uint8_rgb(src.read(window=window))
    return png_bytes(rgb), {"modality": "rgb", "content_type": "image/png"}
```

### `backend/detection_policy.py` (lines 68–122)

Add a `sam3` row to `THRESHOLD_PROFILES`. Reuse the same parent-class thresholds as `lae-dino` (since SAM3 produces the same parent-class taxonomy via prompts). No new parent classes.

---

## Supported Labels (initial list)

**SAM3 text-prompted (the primary, configurable set):**
1. aircraft
2. ship
3. vehicle
4. military_vehicle
5. storage_tank
6. bridge
7. harbor
8. airfield
9. building
10. infrastructure

(Caller can override `metadata.text_prompts` with arbitrary open-vocab phrases at request time.)

**Prithvi pretrained heads (multispectral path only — appended to detections that overlap):**
- `flood`, `water` — from Sen1Floods11
- `burn_scar` — from BurnScars
- `crop:<class>` — from multi-temporal-crop-classification (class names per HF model config; expect ~13 crop/land classes)

**DINOv3** — produces no labels. Each detection carries a frozen `dinov3_embedding` (1024-d for ViT-L, 4096-d for ViT-7B) for downstream similarity/dedup.

---

## Verification

1. **Health**:
   ```bash
   docker compose --profile sam3 up -d inference-sam3
   curl http://localhost:8008/health
   # → {"status":"ok","models":{"sam3":"loaded","dinov3":"loaded","prithvi_backbone":"loaded","prithvi_heads":["flood","burnscars","crop"]}}
   ```
2. **Text-prompted RGB detect**:
   ```bash
   curl -F image=@sample/chicago_chip.png \
        -F 'metadata={"text_prompts":["a ship","an airplane"]}' \
        http://localhost:8008/detect | jq '.detections[].original_class'
   ```
3. **Box-prompted (SAM2-compatible) detect**:
   ```bash
   curl -F image=@sample/chicago_chip.png \
        -F 'metadata={"prompt_boxes":[[10,10,200,200]],"prompt_labels":["ship"]}' \
        http://localhost:8008/detect | jq '.detections[].mask_rle'
   ```
4. **Multispectral detect** (after backend chip path is in place):
   ```bash
   curl -F image=@sample/hls6_chip.tif \
        -F 'metadata={"modality":"multispectral","prithvi_tasks":["flood","burnscar"]}' \
        http://localhost:8008/detect | jq '.detections[].prithvi_labels'
   ```
5. **End-to-end**: upload a multi-band raster via `POST /api/ingest/upload` with `inference_providers=["yolo","sam3"]`. Verify in PostGIS that detections include `mask_rle`, `dinov3_embedding`, and (for HLS imagery) `prithvi_labels`.
6. **Smoke parity**: extend the `inference-sam2/smoke_*.py` style probe; add as a CI step.
7. **Pytest**: `pytest inference-sam3/tests/` — health, text-prompt, box-prompt, multispectral.

---

## Critical Files

**New:**
- `inference-sam3/Dockerfile.gpu`, `requirements.txt`
- `inference-sam3/main.py`
- `inference-sam3/multispectral.py`
- `inference-sam3/prompts.py`
- `inference-sam3/prithvi_heads.py`
- `inference-sam3/embedding.py`
- `inference-sam3/probes/probe_chip.png`
- `inference-sam3/tests/{test_health,test_text_prompt,test_box_prompt,test_multispectral}.py`

**Modified:**
- [docker-compose.yml](../docker-compose.yml) — new service `inference-sam3`, new volume `sam3_models`, new env vars on `backend` and `worker`
- [backend/worker.py](../backend/worker.py) (lines 32–44) — register provider, extend `GROUNDED_SEGMENTERS` and `CONSENSUS_EXEMPT_PROVIDERS`, add multispectral chip-emit branch
- [backend/detection_policy.py](../backend/detection_policy.py) (lines 68–122) — add `sam3` row in `THRESHOLD_PROFILES`
- [backend/main.py](../backend/main.py) — wire `INFERENCE_SAM3_URL` to provider registry
- [README.md](../README.md) — document new endpoint, env vars, sample requests
- `.env.example` — add `HF_TOKEN`, `SAM3_*`, `DINOV3_MODEL_ID`, `PRITHVI_BACKBONE_ID`, `SAM3_GPU_PROFILE`

## Reusable Utilities

- [inference-sam2/main.py:56-216](../inference-sam2/main.py#L56-L216) — multi-GPU pool + `_auto_cuda_devices` (copy/adapt)
- [inference-sam2/main.py:465-470](../inference-sam2/main.py#L465-L470) — `decode_image` helper
- [inference-sam2/main.py:472-517](../inference-sam2/main.py#L472-L517) — `/detect` and `/health` endpoint shape
- [inference-lae-dino/main.py:950-1065](../inference-lae-dino/main.py#L950-L1065) — internal tiling + cross-tile NMS (adapt to mask-aware NMS)
- [backend/worker.py:350-359](../backend/worker.py#L350-L359) — `chip_to_uint8_rgb` (RGB preview parity for HLS)
- [backend/worker.py:362-419](../backend/worker.py#L362-L419) — `valid_data_mask`, `clip_box_to_valid_mask`
- [backend/detection_policy.py:10-122](../backend/detection_policy.py#L10-L122) — `active_detection_policy`, `detection_decision`, `THRESHOLD_PROFILES`

## Risks & Open Items

- **HF gated repos**: `facebook/sam3.1` and `facebook/dinov3-vit*` require an approved HF account. The build will fail without `HF_TOKEN` and prior access approval. README must call this out.
- **DINOv3 license**: Meta's custom license — internal/research use is generally OK, but anyone redeploying should read it before commercial deployment.
- **VRAM**: ViT-L default + SAM3 (848M) + Prithvi-2.0-600M-TL + 3 downstream heads ≈ 6–8 GB FP16. ViT-7B opt-in pushes to ~20 GB. Document GPU profile expectations.
- **Multispectral chips**: many existing rasters are 3-band; the multispectral path only fires when `src.count >= 6` and HLS-band naming matches. Otherwise SAM3 + DINOv3 still run on the RGB path; Prithvi simply skipped.
- **No prototype banks**: per the user's answer, we do *not* build local label embedding banks. DINOv3 vectors are stored only for future similarity work; they do not influence labels in this version.
- **Plan delivery**: per the user's answer, on approval we only write the final plan content to `ProjectPlan/sam3-cc.md`. No code changes, no docker-compose edits, no implementation.
