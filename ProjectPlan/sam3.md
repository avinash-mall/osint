# SAM3 Multi-Modality Inference Endpoint — Unified Plan

> Synthesizes [sam3-cc.md](sam3-cc.md), [sam3-cdx.md](sam3-cdx.md), and [sam3-gmn.md](sam3-gmn.md), and grounds every architectural decision in upstream model documentation.
> Validated and corrected on 2026-05-07 against the upstream repos/model cards linked in §13. Major corrections: SAM3 uses Meta's SAM License rather than Apache-2.0; image/chip inference uses `facebook/sam3` while SAM3.1 is the Object Multiplex video checkpoint; official SAM3 has no native OBB/SAR/multispectral/geospatial checkpoint, so OBBs are deterministic mask geometry; Prithvi flood/burn packaged inference uses 512-pixel windows even though the training config includes 224-pixel resize transforms; the Prithvi crop model requires 18 bands (3 timestamps × 6 bands); TerraMind generation outputs do not include `RGB`, so SAR uses `S1GRD -> S2L2A` plus RGB rendering from generated optical bands.
> All weights are pretrained — **no training, no fine-tuning, no local prototype banks, no pseudo-label training loop**. Adds first-class support for **RGB / multispectral / SAR / FMV (video)** modalities and **open-vocabulary** object detection, classification/labelling, masks, and oriented bounding boxes (OBB).

---

## 0. Goal & Non-Goals

**Goal.** Build one new FastAPI inference service `inference-sam3` that, for any chip the Celery worker dispatches, returns **open-vocabulary object detections** with class labels, original text labels, confidence, masks, HBBs, OBBs, and frozen embeddings, using only published pretrained weights. The service handles four input modalities — Optical RGB satellite/aerial, Multispectral HLS / Sentinel-2 (6+ bands), Sentinel-1 SAR (VV/VH), and FMV drone/video clips — over a single `/detect` (and `/detect_video`) contract. Output integrates with the existing PostGIS detection pipeline ([backend/worker.py:38-44](../backend/worker.py#L38-L44)) and respects the existing two-phase grounded-provider dispatch ([backend/worker.py:729-849](../backend/worker.py#L729-L849)).

**Primary output contract.** Every detection should be useful as an object-detection result even without the mask viewer:
- `class` / `parent_class`: coarse routing class from `detection_policy.py`.
- `original_class`: exact SAM3 prompt text, preserving open-vocabulary labelling.
- `bbox`: normalized HBB `[cx, cy, w, h]` for existing UI/database compatibility.
- `obb`: normalized eight-corner OBB `[x1,y1,x2,y2,x3,y3,x4,y4]` derived from the mask; this is the canonical geometry for satellite/aerial/FMV object work.
- `mask_rle`: retained because mask quality is what makes the OBB derivation possible.
- GIS exports additionally compute a **map-space OBB** from polygonized masks in a projected CRS; do not treat the pixel-space `obb` as the only authoritative geospatial artifact.

**Open-vocabulary by construction.** The text prompt *is* the label. SAM3 is trained on **~4 M unique noun-phrase concepts**; the platform reflects that — it has no closed taxonomy, no per-class threshold, and no distractor suppression. Default prompt sets are **per-modality auto-selected** from public benchmark vocabularies (LVIS+COCO+Objects365 for ground/FMV; xView+DOTA+DIOR+fMoW for satellite/aerial). Callers may override with arbitrary noun phrases at any time.

**Non-Goals.**
- No training, fine-tuning, head-fitting, distillation, anchor-bank construction, or prototype averaging.
- No downstream YOLO/MMRotate/LSKNet training from SAM3 pseudo-labels. Exporters may write DOTA/YOLO-OBB files, but they are for analysis/interchange only unless a future project explicitly permits training.
- No closed taxonomy and no defence-only label set.
- No new ground-truth ingestion or label-studio integration.
---

## 1. Models — What We Use & Why (Researched)

### 1.1 SAM 3 / SAM 3.1 — primary labeller & segmenter

| Fact | Source |
|---|---|
| Repo: `facebookresearch/sam3`. Released November 2025; current repo update notes list SAM 3.1 on 2026-03-27. | [GitHub README](https://github.com/facebookresearch/sam3) |
| Code and weights are under Meta's **SAM License**, not Apache-2.0. The `facebook/sam3` and `facebook/sam3.1` checkpoint repos are gated and marked `License: other`. | [GitHub LICENSE](https://github.com/facebookresearch/sam3/blob/main/LICENSE), [HF `facebook/sam3`](https://huggingface.co/facebook/sam3), [HF `facebook/sam3.1`](https://huggingface.co/facebook/sam3.1) |
| Promptable Concept Segmentation: text + image-exemplar prompts. Returns `{"masks","boxes","scores"}`. | [GitHub README](https://github.com/facebookresearch/sam3/blob/main/README.md) |
| Trained on SA-Co dataset: **~4 M unique noun-phrase concepts** (38 M synthetic). The model is open-vocabulary — the prompt *is* the label. | [arXiv 2511.16719](https://arxiv.org/html/2511.16719v1) |
| README image results report LVIS mask AP 48.5 and COCO box AP 56.4 / SA-Co Gold mask cgF1 54.1. | [GitHub README](https://github.com/facebookresearch/sam3#image-results) |
| **Image API (native repo `facebookresearch/sam3`):** `from sam3.model_builder import build_sam3_image_model; from sam3.model.sam3_image_processor import Sam3Processor` → `state = processor.set_image(image)`. Text: `out = processor.set_text_prompt(prompt="...", state=state)`. Box: `out = processor.add_geometric_prompt(box=[cx,cy,w,h normalized], label=True, state=state)`. Both return state with `out["masks"]` (bool), `out["boxes"]` (pixel xyxy), `out["scores"]`. The state caches backbone features so per-prompt cost is encoder-free after the first call. | [SAM3 GitHub README](https://github.com/facebookresearch/sam3/blob/main/README.md) |
| **Prompt conventions** (native box prompts): `label=True` for a positive box, `label=False` for a negative box. Input boxes are normalized `[center_x, center_y, width, height]` in `[0, 1]`; output boxes are absolute pixel `xyxy`. | [native `Sam3Processor.add_geometric_prompt` docstring](https://github.com/facebookresearch/sam3/blob/main/sam3/model/sam3_image_processor.py) |
| **Video API:** plain SAM 3 → `build_sam3_video_predictor()`; SAM 3.1 Object Multiplex → `build_sam3_multiplex_video_predictor()`. **Both take no model_id / checkpoint_path arguments** — the HF checkpoint is fetched internally. Session API: `predictor.handle_request({"type":"start_session"\|"reset_session"\|"add_prompt"\|"remove_object"\|"close_session", ...})`; propagation is **streaming**: `for resp in predictor.handle_stream_request({"type":"propagate_in_video","session_id":...}): resp["frame_index"], resp["outputs"]`. | [`sam3.1_video_predictor_example.ipynb`](https://github.com/facebookresearch/sam3/blob/main/examples/sam3.1_video_predictor_example.ipynb) |
| HF/Transformers docs state SAM3 is meant for **1008 px** inference; output boxes are `xyxy`, and post-processed boxes are absolute pixel coordinates. | [Transformers SAM3 docs](https://huggingface.co/docs/transformers/en/model_doc/sam3) |
| **Image inference does NOT benefit from Object Multiplex.** Multiplex is a video-tracking optimization. For images, the throughput optimization is vision-feature caching (above). | [RELEASE_SAM3p1.md](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md) |
| There is no official Meta checkpoint specialized for satellite imagery, multispectral imagery, SAR, geospatial metadata, or native OBB output. Satellite/SAR support in this plan is preprocessing + pretrained auxiliary EO models + deterministic geometry. | [GitHub README](https://github.com/facebookresearch/sam3), [HF `facebook/sam3`](https://huggingface.co/facebook/sam3), [Transformers SAM3 docs](https://huggingface.co/docs/transformers/en/model_doc/sam3) |
| **SAM 3.1 Object Multiplex** (2026-03-27): joint multi-object tracking ~7× faster at 128 objects on H100; +2.0 cgF1 on YT-Temporal-1B; +2.0 MOSEv2. | [RELEASE_SAM3p1.md](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md) |
| Stack: Python ≥ 3.12, PyTorch ≥ 2.7, CUDA ≥ 12.6. | [GitHub README prerequisites](https://github.com/facebookresearch/sam3/blob/main/README.md#prerequisites) |

**Decision.** Use `facebook/sam3` for image/chip segmentation and `facebook/sam3.1` for FMV Object Multiplex tracking. Treat the text prompt as the *label*. The "all possible labels" requirement is satisfied trivially: callers may pass arbitrary noun phrases at request time, and the worker auto-selects a per-modality default prompt set from public benchmark vocabularies (Sec. 4).

### 1.2 DINOv3 — frozen embedder (image + per-track)

| Fact | Source |
|---|---|
| Repo `facebookresearch/dinov3`; weights on HF; **gated** under DINOv3 license. | [GitHub](https://github.com/facebookresearch/dinov3), [HF card](https://huggingface.co/facebook/dinov3-vitl16-pretrain-sat493m) |
| **Two pretraining datasets:** LVD-1689M (general web 1.69 B images) and **SAT-493M (Maxar RGB ortho 0.6 m GSD, 493 M × 512×512 chips)**. | [HF SAT-493M card](https://huggingface.co/facebook/dinov3-vit7b16-pretrain-sat493m) |
| **Sat-trained variants:** `facebook/dinov3-vit7b16-pretrain-sat493m` (≈6.7 B params) and `facebook/dinov3-vitl16-pretrain-sat493m` (≈300 M params, distilled from 7B). | [HF SAT-493M card](https://huggingface.co/facebook/dinov3-vit7b16-pretrain-sat493m) |
| GEO-Bench numbers for SAT variant: 79.6 mean classification, 74.5 mean segmentation — Meta explicitly recommends SAT-493M for satellite remote sensing. | [HF card](https://huggingface.co/facebook/dinov3-vitl16-pretrain-sat493m) |
| **General variants** (LVD-1689M): `dinov3-vits16`, `dinov3-vitb16`, `dinov3-vitl16`, `dinov3-vith16plus`, `dinov3-vit7b16`. | [DINOv3 GitHub README](https://github.com/facebookresearch/dinov3#pretrained-models) |
| Output: `outputs.last_hidden_state[:, 0, :]` for CLS, or `outputs.pooler_output` (D = 1024 for ViT-L). | [HF card](https://huggingface.co/facebook/dinov3-vitl16-pretrain-sat493m) |

**Decision.**
- **Default for satellite/aerial RGB chips:** `facebook/dinov3-vitl16-pretrain-sat493m` (300 M, ~600 MB FP16, trained on Maxar at 0.6 m). This is a strict upgrade over the LVD-1689M choice in the three reference plans.
- **Default for FMV:** `facebook/dinov3-vitl16-pretrain-lvd1689m` (300 M). FMV ground/oblique imagery is not satellite-domain; the LVD pre-training transfers better.
- **Opt-in 7 B:** both `dinov3-vit7b16-pretrain-sat493m` and `dinov3-vit7b16-pretrain-lvd1689m` available via env override; ~14 GB FP16.
- **Use:** frozen embedding only — no labels are derived from DINOv3. Vector is stored on each detection for similarity/dedup search. **No prototype banks** (per user constraint).

### 1.3 Prithvi-EO-2.0 — multispectral labelling (optical-only)

| Fact | Source |
|---|---|
| Backbones: `Prithvi-EO-2.0-300M`, `Prithvi-EO-2.0-300M-TL`, `Prithvi-EO-2.0-600M`, `Prithvi-EO-2.0-600M-TL`, `Prithvi-EO-2.0-100M-TL`, `Prithvi-EO-2.0-tiny-TL`. Apache-2.0, **no gating**. | [`ibm-nasa-geospatial`](https://huggingface.co/ibm-nasa-geospatial) |
| Inputs: 6 HLS bands — **Blue, Green, Red, Narrow-NIR, SWIR-1, SWIR-2**. The training config uses `constant_scale: 0.0001` and 224×224 Albumentations resize transforms; the released flood and burn inference scripts instead run 512-pixel sliding windows and divide DN by 10000 when needed. | [Sen1Floods11 config](https://github.com/NASA-IMPACT/Prithvi-EO-2.0/blob/main/configs/sen1floods11.yaml), [Sen1Floods11 inference.py](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11/blob/main/inference.py), [BurnScars inference.py](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars/blob/main/inference.py) |
| TL ("Transfer Learning") variants accept temporal + lat/lon metadata as conditioning tokens. | [Prithvi-EO-2.0 paper](https://arxiv.org/abs/2412.02732) |
| **Pretrained downstream heads (ready-to-run, no training):** | [`ibm-nasa-geospatial`](https://huggingface.co/ibm-nasa-geospatial) |
| `ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11` — water/flood segmentation over six Sentinel-2 bands; labels are no-water=0, water/flood=1, and no-data/clouds=-1 (ignored label, not a learned third class). | [HF card](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11), [config `num_classes: 2`](https://github.com/NASA-IMPACT/Prithvi-EO-2.0/blob/main/configs/sen1floods11.yaml) |
| `ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars` — binary burn-scar segmentation. | [HF org](https://huggingface.co/ibm-nasa-geospatial) |
| `ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification` — 13-class HLS/CDL crop/landcover segmentation, but it requires **18 input bands** (3 timesteps × 6 HLS bands), not a single 6-band chip. | [HF crop card](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification) |
| `ibm-nasa-geospatial/Prithvi-EO-1.0-100M-sen1floods11` — older 100 M flood model (fallback). | [HF org](https://huggingface.co/ibm-nasa-geospatial) |
| Released inference scripts load the task through `terratorch.cli_tools.LightningInferenceModel.from_config(config, checkpoint)`. | [Sen1Floods11 inference.py](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11/blob/main/inference.py) |

**Decision.** Use the 600M-TL backbone for embeddings on multispectral chips, and run the two single-timestamp published downstream segmentation heads as overlay-labellers (`water/flood`, `burn_scar`). Use the crop model only when the worker can provide the documented 18-band, 3-timestep HLS stack; otherwise omit `crop:*` labels rather than fabricating a crop map from one timestamp. Prithvi is optical-only; SAR goes to TerraMind.

> **Correction over the reference plans.** Do not state a single Prithvi "native inference size." The fine-tuning YAML uses 224×224 transforms, while the released flood/burn inference scripts explicitly use 512-pixel windows. The implementation should mirror the released inference scripts for downstream heads and keep the 224/patch-token assumption only where the backbone API requires it.

### 1.4 TerraMind v1 — SAR + multimodal generative reasoning

| Fact | Source |
|---|---|
| `ibm-esa-geospatial/TerraMind-1.0-large` — first any-to-any generative EO foundation model (IBM × ESA × Jülich, 2025-04). Apache-2.0, **not gated**. | [HF card](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-large) |
| Modalities supported in one model: `S2L2A` (12-band), `S2L1C` (12-band), **`S1GRD` (2-band SAR)**, `S1RTC`, `DEM`, `RGB`. | [HF card](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-large) |
| Variants on HF: tiny / small / base / large / large-tim. | [TerraMind announcement](https://research.ibm.com/blog/terramind-esa-earth-observation-model) |
| Trained on TerraMesh, 9 M aligned S1+S2 sample tuples, 500 B tokens. | [arXiv 2504.11171](https://arxiv.org/html/2504.11171v1) |
| ESA PANGAEA benchmark: TerraMind beats prior EO foundation models by **≥ 8 %** on land-cover, change detection, multi-sensor tasks. | [IBM Research blog](https://research.ibm.com/blog/terramind-esa-earth-observation-model) |
| Loaded via TerraTorch: `BACKBONE_REGISTRY.build("terramind_v1_large", pretrained=True, modalities=["S1GRD"])`. | [HF card](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-large) |
| Patch embeddings shape: `(B, 196, 768)` for 224×224 input. Backbone handles missing modalities. | [HF card](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-large) |
| Supports any-to-any **generation**. TerraTorch docs list generation output modalities as `S2L2A`, `S1GRD`, `S1RTC`, `DEM`, `LULC`, `NDVI`, and `Coordinates`; `RGB` is a raw input modality, not a documented output modality. | [HF card](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-large), [TerraTorch TerraMind guide](https://terrastackai.github.io/terratorch/1.1/guide/terramind/) |

**Decision.**
- TerraMind is the SAR specialist. For SAR chips (Sentinel-1 VV/VH, 2-band), TerraMind:
  1. Extracts a 768-d patch embedding (used as the SAR equivalent of `dinov3_embedding`).
  2. Generates an optical proxy via the `terramind_v1_large_generate` head with `output_modalities=["S2L2A"]`, then renders an RGB preview from the generated Sentinel-2 Blue/Green/Red bands. The preview is what we feed SAM3 (which has no SAR pre-training).
- For multispectral chips, TerraMind is *not* the default — Prithvi heads cover the canonical EO overlays we can run without training (water/flood and burn; crop only when the documented 18-band temporal stack exists), and the chip is already RGB-renderable. TerraMind stays available behind an env flag (`SAM3_USE_TERRAMIND_MS=1`) for future fusion experiments.
- For SAR labelling: SAM3 runs on the generated optical/RGB proxy (no native open-vocab SAR foundation model in this plan ships pretrained label heads usable as is). The SAR detections set `metadata.sar_proxy=true`, cap confidence, and remain review candidates.

### 1.5 Models considered & rejected (with reason)

| Model | Why not |
|---|---|
| Clay v1.5 (`made-with-clay/Clay`) | Apache-2.0, supports S1+S2+more. **No published downstream label heads** — would require fine-tuning to extract labels, which violates the "no training" constraint. Useful as a fallback embedder; we stash the model id but do not load it by default. ([Clay HF](https://huggingface.co/made-with-clay/Clay)) |
| TerraFM | SOTA on PANGAEA but ships no ready-to-run multi-class label heads at HF; the [paper](https://arxiv.org/html/2506.06281v1) describes evaluation pipelines rather than packaged heads. |
| CROMA | Multimodal contrastive (S1/S2). Embedder only; no labels. |
| GroundingDINO / OWLv2 | Open-vocab detectors, but **box-only** (no masks). SAM3 strictly dominates them on COCO/LVIS while also returning masks; using them in addition would just add latency. |
| SAM2.1 | Already deployed as a separate service. SAM3 is a superset of SAM2 capabilities (image + video, plus text prompts) — we keep SAM2 as a fallback container but route new work to SAM3. |
| YOLO-World / YOLOE | Box-only, smaller vocab, no foundation embeddings. |
| Snow-avalanche SAM-SAR adapter ([MDPI 18/3/519](https://www.mdpi.com/2072-4292/18/3/519)) | Single-task fine-tune; not a general SAR head. |

### 1.6 Toolchain lessons from `SAM 3 Satellite OBB Labeling Options.md`

| Toolchain | What we keep | What we reject |
|---|---|---|
| HBB2OBB | Its proven conversion pattern: scale/clip region, refine mask, largest contour, `minAreaRect`, HBB fallback, OBB export/evaluation. | Importing it as a hard runtime dependency. We already have SAM3 masks, so the core geometry is implemented locally in `fusion.py`. |
| Ultralytics OBB | Its normalized four-corner YOLO OBB export format. | YOLO-OBB training or pseudo-label distillation; that violates the current no-training objective. |
| SamGeo3 / QGIS | Tiling/georeferencing lessons for large GeoTIFFs and GeoJSON-style output. SamGeo's SAM3 tiled example documents overlapping tiles, merged masks, and georeference preservation. | Replacing the existing worker tiler or adding an analyst GUI to the runtime path. |
| geosam | Confirmation that a geospatial SAM3 wrapper can accept GeoTIFF imagery and automatically chunk large images. | R runtime dependency in this Python service; use it only as a reference path for GIS/R users. |
| SegEarth-OV3 | Research signal for remote-sensing open-vocabulary false-positive reduction: mask fusion plus presence-guided filtering over large scenes. | Default dependency until license, reproducibility, offline cache, and runtime complexity are verified; it is not a turnkey OBB labeler. |
| X-AnyLabeling / CVAT | Useful optional human review tools for inspecting masks/OBBs. | Runtime dependency. They are annotation UIs, not the production detector. Treat community ONNX SAM3 exports as convenience artifacts only because their license/provenance metadata can conflict with upstream SAM License terms. |
| Roboflow Auto-Label | Nothing for production. | Cloud processing and hosted data flow; incompatible with offline deployment. |

---

## 2. Architecture

### 2.1 Service topology

```
                        ┌────────────────────────────────────────────────────────┐
                        │  backend/worker.py  (Celery)                            │
                        │  • slice_and_infer()  for IMAGERY                       │
                        │  • process_fmv_clip() for FMV (NEW — Sec 8.4)           │
                        └──────────┬──────────────────────────────────────────────┘
                                   │ HTTP multipart/form-data
        ┌──────────────┬───────────┼───────────────┬──────────────┬──────────────────────┐
        ▼              ▼           ▼               ▼              ▼                      ▼
   yolo:8001    lae-dino:8001   mmrotate:8001  lsknet:8001   sam2:8001        inference-sam3:8001 ★ NEW
                                                                                   │
       ┌─────────────────────────┬────────────────────┬─────────────────────────────┴───────────────────────┐
       ▼                         ▼                    ▼                                                     ▼
  SAM3 image+video         DINOv3 (frozen)      Prithvi heads (optical MS)                       TerraMind (SAR / fusion)
  image: facebook/sam3     • SAT-493M ViT-L       • Sen1Floods11 (water/flood)                   ibm-esa-geospatial/
  video: facebook/sam3.1
  • native Sam3Processor   • LVD-1689M ViT-L       • BurnScars (burn_scar)                       TerraMind-1.0-large
  • multiplex_video_pred.  • opt-in 7B            • crop head only with 3-timestep HLS             • S1GRD backbone
                           • cls/pooler vec       (optical only)                                  • S1GRD→S2L2A→RGB preview
```

### 2.2 Per-modality request pipeline

```
                     +-------------------------------------------------+
   request -->  ┌─── │  Detect modality (metadata + raster sniff)      │
                │    └─────────────────────┬───────────────────────────┘
                │                          │
                │ ┌──────────────┬─────────┼──────────────┬────────────────────────────────┐
                ▼ ▼              ▼         ▼              ▼                                 ▼
         ┌──────────┐    ┌──────────────┐   ┌──────────────┐                       ┌────────────────┐
         │  RGB     │    │ MULTISPECTRAL│   │     SAR      │                       │     FMV        │
         │  PNG/JPG │    │  6+ band TIF │   │  S1GRD 2-band│                       │  MP4/TS/MOV    │
         └────┬─────┘    └──────┬───────┘   └──────┬───────┘                       └───────┬────────┘
              │                 │                  │                                       │
        chip3 = decode    chip6,chip3=multi    chip2 = sar.decode_s1                 frames = video.iter
              │                 │                  │                                       │
              │                 │       chip3 = terramind.s1_to_s2_rgb(chip2)              │
              │                 │                  │                                       │
              ▼                 ▼                  ▼                                       ▼
        sam3.image      sam3.image            sam3.image                          sam3.video.add_prompt
        +dinov3-sat     +dinov3-sat           +dinov3-sat                         +dinov3-lvd per frame
              │           +prithvi(water,           +terramind embed                       │
              │            burn; crop iff           (768d)                                 │
              │            18-band stack)                                                   │
              ▼                 ▼                  ▼                                       ▼
        normalized   normalized + extra      normalized + sar_proxy=true            per-frame detections
        detections   prithvi_labels          + terramind_embedding                  + track_id (Object Multiplex)
                                                                                    + per-track dinov3
```

### 2.3 Modality auto-detect rules (worker side)

The worker decides modality from raster metadata before dispatching the chip:

| Detected | Rule | Chip emit |
|---|---|---|
| RGB | `src.count == 3` OR file is PNG/JPEG | uint8 RGB PNG, `metadata.modality="rgb"` |
| MULTISPECTRAL | `src.count >= 6` AND HLS/Sentinel-2 band names present (any of `B02..B12, B8A`) | float32 6-band GeoTIFF, `metadata.modality="multispectral"`; if 18 temporally ordered bands are available, also set `metadata.hls_timesteps=3` for crop overlays |
| SAR | `src.count == 2` AND `src.descriptions` indicate VV/VH, or sensor metadata says Sentinel-1 GRD with VV/VH | float32 2-band GeoTIFF, `metadata.modality="sar"`, plus `metadata.sar_polarizations=["VV","VH"]`; HH/HV should not enter the TerraMind S1GRD path unless a separate compatibility test is added |
| FMV | `Content-Type` is video/* OR file is MP4/MOV/TS/AVI/MPEG-TS | not chipped — sent to `/detect_video` instead of `/detect` (Sec 6.4) |

If the auto-detect is ambiguous, caller can override via `metadata.modality`.

For georeferenced imagery, every chip payload also carries `metadata.geo`:

```json
{
  "source_crs": "EPSG:32640",
  "chip_transform": [703000.0, 0.6, 0.0, 2770000.0, 0.0, -0.6],
  "chip_transform_order": "gdal",
  "source_window": [2048, 1024, 1024, 1024],
  "source_bounds": [703000.0, 2769385.6, 703614.4, 2770000.0]
}
```

This is required for geospatial OBB export. GDAL defines geotransforms as six-coefficient affine transforms from pixel/line coordinates to projected or geographic coordinates, including optional rotation terms, so the worker must preserve the actual `window_transform` rather than only the source bounds.

GeoTIFF remains the safest interchange format. Cloud Optimized GeoTIFFs are acceptable as source rasters when they are available locally or through an approved connected preparation step; the offline runtime should read windows from local COG/GeoTIFF files and should not fetch remote tiles.

### 2.4 Where labels come from

| Source | Label space | How |
|---|---|---|
| **SAM3 text prompts** | **Open vocabulary** — the prompt *is* the label. Defaults below. Caller can override per request. | Encode chip once via `state = processor.set_image(image)`, loop prompts → `processor.set_text_prompt(prompt=…, state=state)` → `state["masks"], state["boxes"], state["scores"]`. The state caches backbone features so per-prompt cost is encoder-free. |
| **Prithvi-Sen1Floods11** | `water` (source model class is water/flood) | 6-band MS chip → 2 learned classes plus ignored no-data/cloud label; intersect water/flood mask with SAM3 boxes. |
| **Prithvi-BurnScars** | `burn_scar` | 6-band MS chip → binary segmentation; intersect with SAM3 boxes. |
| **Prithvi-multi-temporal-crop** | 13 crop/landcover classes (`crop:natural_vegetation`, `crop:forest`, `crop:corn`, `crop:soybeans`, `crop:wetlands`, `crop:developed_barren`, `crop:open_water`, `crop:winter_wheat`, `crop:alfalfa`, `crop:fallow_idle_cropland`, `crop:cotton`, `crop:sorghum`, `crop:other`) | Only when an 18-band, 3-timestep HLS stack is present → per-pixel argmax; majority class inside SAM3 box → `crop:<class>`. |
| **DINOv3 / TerraMind** | (no labels) | Embedding only. |
| **SAM3 video tracker** | label inherited from per-track text prompt | One prompt creates one track; all frames get the same `class`. |

When the caller does not override `metadata.text_prompts`, the service auto-selects a default prompt set based on `metadata.modality` (Sec 4) — `satellite_v1` (xView+DOTA+DIOR+fMoW) for satellite/aerial RGB, multispectral, and SAR; `ground_v1` (LVIS+COCO+Objects365) for FMV. There is no defence-only or closed taxonomy.

### 2.5 Per-detection storage

`detections.metadata` (existing JSONB column) gets:

```jsonc
{
  "mask_rle": "<base64 COCO RLE>",
  "obb": [x1,y1,x2,y2,x3,y3,x4,y4],          // normalized 8-corner OBB
  "obb_format": "yolo_obb_normalized_xyxyxyxy",
  "obb_source": "mask_min_area_rect",
  "obb_angle_deg": -37.4,
  "obb_area_px": 812.5,
  "edge_truncated": false,
  "geo": {
    "source_crs": "EPSG:32640",
    "chip_transform": [703000.0, 0.6, 0.0, 2770000.0, 0.0, -0.6],
    "chip_transform_order": "gdal",
    "source_window": [2048, 1024, 1024, 1024],
    "obb_map_crs": null,
    "obb_map_geojson": null
  },
  "embedding": {
    "model": "dinov3-vitl16-sat493m",
    "dim": 1024,
    "fp16_b64": "<base64 fp16 vector>"
  },
  "prithvi_labels": ["water"],                 // multispectral path only; crop:* only with 18-band temporal input
  "modality": "multispectral",
  "sar_proxy": false,                          // true when SAR-via-RGB-render
  "terramind_embedding": null,                 // set on SAR
  "model_versions": {
    "sam3_image": "facebook/sam3",
    "sam3_video": "facebook/sam3.1",
    "dinov3": "facebook/dinov3-vitl16-pretrain-sat493m",
    "prithvi_backbone": "ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL",
    "terramind": null
  }
}
```

No DDL migration is required — `detections.metadata` is already JSONB. A future `pgvector` column for similarity is mentioned but out of scope.

### 2.6 OBB generation and export contract

SAM3 is a mask-first detector: official examples return instance masks, scores, and axis-aligned `xyxy` boxes, not native rotated boxes. For satellite imagery and drone FMV, OBBs must therefore be **derived deterministically from the mask**. This matches the OBB research in `SAM 3 Satellite OBB Labeling Options.md` and `deep-research-report.md`: segment first, preserve the mask/polygon, then compute the minimum-area rotated rectangle.

**Canonical internal OBB.**
- Store `metadata.obb` as a **pixel-space** YOLO-style normalized four-corner rectangle: `[x1,y1,x2,y2,x3,y3,x4,y4]`, each value clamped to `[0,1]`. [Ultralytics documents](https://docs.ultralytics.com/datasets/obb/) this normalized four-corner structure for YOLO OBB datasets.
- Store companion fields: `metadata.obb_source`, `metadata.obb_format="yolo_obb_normalized_xyxyxyxy"`, `metadata.obb_angle_deg`, `metadata.obb_area_px`, and `metadata.edge_truncated`.
- Keep `bbox` as normalized HBB for existing DB/UI paths. HBB is compatibility geometry; OBB is the primary geospatial geometry.

**Pixel-space mask-to-OBB algorithm.**
1. Clip the SAM3 mask to `valid_data_mask` before geometry extraction, so nodata/black borders cannot drive the OBB.
2. Optionally apply morphological opening with `SAM3_OBB_OPENING_KERNEL_PCT` (default `0.01`) relative to the shorter side of the mask extent. HBB2OBB exposes the same class of parameter as `opening_kernel_percentage`; keep it tunable because the right value depends on sensor GSD and object size.
3. Extract external contours, keep the largest refined contour, and reject contours below `SAM3_OBB_MIN_AREA_PX` (default `4`).
4. Use OpenCV [`cv2.minAreaRect(contour)` and `cv2.boxPoints(rect)`](https://docs.opencv.org/4.x/d3/dc0/group__imgproc__shape.html) to get the minimum-area rotated rectangle and four vertices.
5. If no valid contour survives, fall back to the candidate HBB converted into an axis-aligned four-corner OBB and set `obb_source="hbb_fallback"`. HBB2OBB uses the same operational fallback so downstream records remain structurally complete.
6. If the mask touches the chip boundary or nodata boundary, set `edge_truncated=true`. Do not suppress it automatically; ships, aircraft, and vehicles often sit on tile edges in large rasters.

**Map-space OBB for GIS export.**
- Pixel-space OBB is correct for DOTA/YOLO labels and chip-native UI, especially on north-up square-pixel chips.
- Map-space OBB is required when the analyst needs geospatially correct orientation, dimensions in meters, or vector labels in PostGIS/GIS.
- For map-space OBB, polygonize the instance mask with `rasterio.features.shapes(mask, transform=chip_transform)`, clean/dissolve slivers, reproject to a local projected CRS when angle/size matters, then compute Shapely `minimum_rotated_rectangle` / `oriented_envelope` or PostGIS `ST_OrientedEnvelope`.
- Do **not** compute metric angle/width/height in EPSG:4326, and do not assume raw pixel coordinates are map coordinates. GDAL geotransforms can include scale, rotation, and shear terms; the worker must carry the real chip affine transform.
- If the geospatial conversion returns a degenerate geometry (point/line) or CRS metadata is missing, keep the pixel-space OBB and set `metadata.geo.obb_map_geojson=null` plus an export warning.

**Export formats.**
- **Database/API:** normalized eight-corner OBB in `metadata.obb`.
- **DOTA export:** absolute pixel or georeferenced image coordinates as `x1 y1 x2 y2 x3 y3 x4 y4 class_name difficult`.
- **YOLO OBB export:** `class_index x1 y1 x2 y2 x3 y3 x4 y4` with normalized coordinates, matching Ultralytics' documented OBB dataset format.
- **GeoJSON pixel-corner export:** polygon feature with `metadata.obb` corners transformed through the chip/raster affine transform; this is fast and useful for review.
- **GeoJSON GIS-grade export:** polygonize the mask and compute a Shapely/PostGIS oriented envelope in a projected CRS; this is the authoritative map-space OBB path for geospatial labels.

---

## 3. Modality Specifications

### 3.1 Optical RGB satellite / aerial

- **Input:** 3-band uint8 PNG (worker emits via [chip_to_uint8_rgb](../backend/worker.py#L350-L359)).
- **Path:** SAM3 image (text prompts → masks/boxes/scores) → DINOv3-SAT embedding per detection.
- **Default chip size:** worker tiling stays at `INFERENCE_CHIP_SIZE` (1024) with `INFERENCE_CHIP_OVERLAP` (256) — same as today — while the SAM3 processor/model path uses the upstream 1008 px image canvas. Always post-process masks/boxes back to the original chip dimensions before computing `bbox`, pixel OBB, or map OBB.
- **OBB:** derive `metadata.obb` from each SAM3 mask via Sec 2.6. This is the primary satellite/aerial geometry; HBB is compatibility geometry.
- **No min-box filter:** every SAM3 mask above `SAM3_TEXT_THRESHOLD` is emitted; tiny detections survive and surface in the analyst review queue.

### 3.2 Multispectral (HLS, Sentinel-2 L2A)

- **Input bands:** **Blue, Green, Red, Narrow-NIR, SWIR-1, SWIR-2** in that order, float32 GeoTIFF.
- **Preprocessing:** convert DN to reflectance with `arr / 10000` / `constant_scale: 0.0001` when source values are in scaled integer form. For the released Prithvi flood/burn heads, mirror the packaged inference scripts: pad/tile into 512-pixel windows, apply the model datamodule transform, then stitch predictions back to chip size. SAM3 still runs on a `hls_to_rgb_preview` derived from bands `[2,1,0]` with 2–98 percentile stretch (mirrors [chip_to_uint8_rgb](../backend/worker.py#L350-L359)).
- **Path:**
  1. Run Prithvi flood and burn inference over the 6-band chip using the released 512-window flow; run crop only if `metadata.hls_timesteps == 3` and 18 bands are present.
  2. Generate uint8 RGB/false-color preview at original H×W → SAM3 text prompts → masks/boxes. Official SAM3 is a 3-channel image model, so multispectral data is never passed as a raw 6+ band tensor to SAM3.
  3. For each SAM3 box, compute IoU with each Prithvi overlay; if `>= SAM3_PRITHVI_OVERLAY_THRESHOLD` (default 0.30), append the matching Prithvi label to the detection's `prithvi_labels` array.
  4. DINOv3-SAT embedding from the RGB preview crop.
- **Tile rule:** if `src.count` ≥ 6 we still tile via the existing chip pipeline; Prithvi's inner tiling/padding happens inside the SAM3 service and does not change worker chip size.

### 3.3 SAR (Sentinel-1 GRD, VV/VH)

- **Input bands:** 2-band float32 (VV, VH dB or linear amplitude). Worker emits a 2-band GeoTIFF chip with `metadata.modality="sar"`.
- **Preprocessing:** clip dB to `[-30, 0]` → linear-stretch each band to `[0,1]` → arrange as TerraMind's expected `S1GRD` tensor `(B, 2, 224, 224)`.
- **Path:**
  1. TerraMind backbone (`terramind_v1_large` with `modalities=["S1GRD"]`) → 768-d patch embedding pooled to a single per-chip vector (mean of the 196 patch tokens). Stored as `terramind_embedding`.
  2. TerraMind generation head (`terramind_v1_large_generate`, `output_modalities=["S2L2A"]`, `standardize=True`) → generated Sentinel-2 L2A proxy at 224×224; render an RGB preview from generated Blue/Green/Red bands and upsample with bilinear to the SAR chip resolution. Official SAM3 sees only this 3-channel proxy, never raw VV/VH.
  3. SAM3 text prompts on the generated optical/RGB preview → masks/boxes/scores. Set `sar_proxy=true` and tag review status `review_candidate` regardless of confidence (the proxy may hallucinate; analyst review is mandatory).
  4. DINOv3-SAT embedding from the generated optical/RGB crop (per detection). Optional: store DINOv3-LVD too via `SAM3_DINOV3_DUAL_EMBED=1`.
- **Confidence cap:** SAR-derived detections are capped at `SAM3_SAR_CONF_CAP` (default `0.85`) to reflect proxy uncertainty. Implementation should set `confidence = min(score, cap)` or multiply by a documented factor, but not both.

### 3.4 FMV (video)

- **Endpoint:** `POST /detect_video` (Sec 6.4) — separate from `/detect` because the request body is a video clip path / upload, not a chip.
- **Inputs:**
  - `video` (multipart upload) **or** `metadata.video_path` referencing a path under `FMV_PATH=/data/fmv` (the worker's view of the volume).
  - `metadata.text_prompts` — list of phrases to track.
  - `metadata.frame_stride` — int, default 1 (every frame).
  - `metadata.max_frames` — cap.
  - `metadata.start_frame`, `metadata.end_frame` — clip range.
- **Path:**
  1. `predictor = build_sam3_multiplex_video_predictor()` (Object Multiplex path; the function takes no arguments — see `examples/sam3.1_video_predictor_example.ipynb`). For plain SAM 3 use `build_sam3_video_predictor()` instead.
  2. `session = predictor.handle_request(request={"type":"start_session","resource_path":video_path})`.
  3. For each text prompt, `add_prompt` on `frame_index=start_frame` → SAM 3.1 Object Multiplex propagates one track per matching instance through subsequent frames in shared memory.
  4. Iterate frames via the **streaming** call `for resp in predictor.handle_stream_request(request={"type":"propagate_in_video","session_id":session_id})`. Each `resp` carries `resp["frame_index"]` and `resp["outputs"]` (list of per-track entries with `obj_id`, mask, score, prompt text). Map this to detections with `track_id` (= `obj_id`), `frame_index`, `t_seconds`, mask RLE, normalized bbox, OBB, and SAM score. Each track gets a single DINOv3-LVD embedding from the crop on its first frame.
  5. Cleanup with `predictor.handle_request(request={"type":"close_session","session_id":session_id})`. The endpoint streams an `application/x-ndjson` response, one JSON per frame×track detection, so the worker can persist into `fmv_detections` ([backend/main.py:349-352](../backend/main.py#L349-L352)) without buffering the whole clip in memory.
- **Tracking continuity:** Object Multiplex is the SAM 3.1 multi-object tracker — see [release notes](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md). Track IDs are stable across the clip; ~7× faster than per-object SAM 3 at 128 objects.

---

## 4. Default Prompt Sets — Open Vocabulary, Per-Modality Auto-Selected

The platform is **open-vocabulary**: every text phrase is a valid label. The defaults are **auto-selected per modality** so the same `/detect` call with no prompts still hits the right vocabulary for the data it is processing — satellite/aerial RGB chips get an aerial vocabulary; FMV ground/oblique frames get a ground vocabulary; multispectral gets the aerial set plus Prithvi overlays; SAR gets the aerial set on the generated optical/RGB proxy.

Two profiles are shipped, both built from public benchmark category lists. **No defence-only profile exists.** Redistribution of the generated JSON vocabularies must still be checked against each dataset's license terms before product distribution.

### 4.1 `satellite_v1` — for `modality ∈ {rgb_satellite, multispectral, sar}`

Built by deduplicating the noun-phrase categories from these public benchmarks:

| Source | Classes | Notes |
|---|---|---|
| **xView** | 60 | Public xView Challenge taxonomy — fixed-wing, small/large vehicle, building, container, etc. ([xviewdataset.org](https://xviewdataset.org/)) |
| **DOTA v2** | 18 | Aerial OBB benchmark — plane, ship, vehicle, harbor, bridge, etc. ([captain-whu.github.io/DOTA](https://captain-whu.github.io/DOTA/)) |
| **DIOR** | 20 | Optical aerial detection benchmark. ([gcheng-nwpu.github.io](http://www.escience.cn/people/gongcheng/DIOR.html)) |
| **fMoW** | 62 | Functional Map of the World land-use scenes — airport, port, dam, swimming pool, fairground, etc. ([Functional Map of the World, IARPA](https://github.com/fMoW/dataset)) |
| **FAIR1M** | 37 | Fine-grained airplane / ship / vehicle subcategories. ([arXiv 2103.05569](https://arxiv.org/abs/2103.05569)) |
| **HRSC2016** | 27 fine-grained ship types | Hierarchical ship taxonomy (1 ship-class → 4 ship-categories → 27 ship-types). Use the leaf level for prompts. ([HRSC2016 Kaggle mirror](https://www.kaggle.com/datasets/guofeng/hrsc2016), [HRSC2016_SOTA repo](https://github.com/ming71/HRSC2016_SOTA)) |
| **RarePlanes** | 33 attribute values across 10 attributes | Aircraft attributes (length, wingspan, role, propulsion, …) rather than discrete detection classes. We materialize the 33 attribute *values* (e.g. `"jet propulsion"`, `"low wing"`, `"single engine"`) as additional noun phrases. ([RarePlanes paper, IQT WACV 2021](https://www.iqt.org/library/the-rareplanes-dataset)) |

After dedupe + lowercase normalisation, the generated count is recorded in `inference-sam3/prompts/satellite_v1.json` metadata. Examples include `airplane`, `helicopter`, `ship`, `oil tanker`, `cargo ship`, `truck`, `pickup truck`, `bus`, `train`, `bridge`, `overpass`, `runway`, `helipad`, `harbor`, `dry dock`, `container crane`, `storage tank`, `chimney`, `windmill`, `dam`, `swimming pool`, `tennis court`, `roundabout`, `solar panel`, `wind farm`, `pipeline`, `power line`, `port`, `airport`, …

### 4.2 `ground_v1` — for `modality == fmv`

Built from ground/oblique imagery benchmarks:

| Source | Classes | Notes |
|---|---|---|
| **LVIS v1** | 1 203 | Large vocabulary instance segmentation. ([CVPR 2019](https://openaccess.thecvf.com/content_CVPR_2019/papers/Gupta_LVIS_A_Dataset_for_Large_Vocabulary_Instance_Segmentation_CVPR_2019_paper.pdf)) |
| **COCO 2017** | 80 | Common Objects in Context. ([cocodataset.org](https://cocodataset.org/)) |
| **Objects365 v2** | 365 | ([objects365.org](https://www.objects365.org/)) |

After dedupe and synonym normalization, the generated count is recorded in `inference-sam3/prompts/ground_v1.json` metadata. Do not assume strict subset relationships between LVIS, COCO, and Objects365; the generator should compute the actual union. Capped per request at `SAM3_MAX_PROMPTS_PER_REQUEST=1024` — operators can lower the cap for latency.

### 4.3 Auto-selection rule

```python
def select_default_profile(modality: str) -> str:
    if modality == "fmv":
        return "ground_v1"
    return "satellite_v1"          # rgb / multispectral / sar
```

### 4.4 Override mechanisms

In priority order:

1. `metadata.text_prompts=[...]` — arbitrary list, overrides everything (true open vocabulary — "all possible labels" lives here).
2. `metadata.prompt_profile=satellite_v1|ground_v1|<custom>` — pick a shipped profile or a custom one.
3. `SAM3_LABEL_FILE=/app/prompts/custom.json` — local override file shipped with the deployment.
4. **Auto-select by modality** (Sec 4.3) — used when none of the above are set.

Validation:
- Trim, lowercase-normalize, dedupe preserving order.
- Reject empty list with HTTP 400 ("No labels supplied").
- Cap at `SAM3_MAX_PROMPTS_PER_REQUEST` (default 1024).
- Warn (don't fail) if a prompt > 25 tokens — SAM3 expects short noun phrases.

### 4.5 Latency footprint

SAM3 image inference loops over prompts. The native `Sam3Processor.set_image()` returns a state that caches backbone features so subsequent `set_text_prompt`/`add_geometric_prompt` calls reuse them — encoder-free per prompt on the same chip. **Object Multiplex helps video mode only.** Treat the following as capacity-planning estimates to validate on the deployment GPU, not sourced SLAs:

| Profile | Prompts (post-dedupe, capped at `SAM3_MAX_PROMPTS_PER_REQUEST`) | Per-chip latency |
|---|---|---|
| `satellite_v1` | low hundreds | benchmark locally; native `set_image` state caches backbone features so per-prompt cost is encoder-free |
| `ground_v1` | capped at 1 024 | benchmark locally; same state cache |
| `text_prompts` (caller-tuned) | 1 – 50 | benchmark locally |

Operators who need lower per-chip latency should pass a tuned `text_prompts` list (anywhere from a single phrase up to a few dozen) rather than relying on the bulk profiles.

---

## 5. Service Layout

```
inference-sam3/
├── Dockerfile.gpu
├── requirements.txt
├── main.py                       # FastAPI app: /health, /detect, /detect_video
├── multispectral.py              # HLS-6 decode/normalize + RGB preview
├── sar.py                        # S1 GRD decode/normalize + dB clip
├── prompts/
│   ├── __init__.py
│   ├── loader.py                 # resolve_prompts(metadata) -> list[str]
│   ├── satellite_v1.json
│   └── ground_v1.json
├── prithvi_heads.py              # flood + burn heads; crop only for 18-band HLS temporal stacks
├── terramind.py                  # SAR backbone + S1GRD→S2L2A→RGB preview
├── embedding.py                  # DINOv3 pool + dual-model selector
├── sam3_runner.py                # SAM3 image + video model loaders & inference
├── fusion.py                     # mask-aware NMS, overlay-IoU, RLE encode, OBB
├── exports/
│   └── obb.py                    # DOTA / YOLO-OBB / GeoJSON exporters
├── probes/
│   └── probe_chip.png            # 1024×1024 fixture used by smoke + tests
└── tests/
    ├── conftest.py               # fixtures, monkeypatch loaders to return tiny stand-ins
    ├── test_health.py
    ├── test_text_prompt_rgb.py
    ├── test_box_prompt.py
    ├── test_multispectral.py
    ├── test_sar.py
    ├── test_video.py
    ├── test_prompts_loader.py
    └── test_fusion.py

backend/                           # small edits, listed in §8
├── worker.py
├── detection_policy.py
├── main.py
└── provider_lifecycle.py

docker-compose.yml                 # add inference-sam3 + sam3_models volume
.env.example                       # new SAM3_*, DINOV3_*, PRITHVI_*, TERRAMIND_*
README.md                          # new endpoint section
```

---

## 6. Service Implementation

### 6.1 `main.py` — FastAPI app

```python
import io, json, os, threading, time
from pathlib import Path
from typing import Any
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool
import cv2; cv2.setNumThreads(0)

import multispectral
import sar
import prithvi_heads
import terramind
import embedding
import sam3_runner
import fusion
from prompts.loader import resolve_prompts

app = FastAPI(title="SentinelOS AIP Node — SAM3 Inference")

MODEL_VERSION   = os.getenv("MODEL_VERSION", "sam3-image+sam3.1-video+dinov3-sat-l+prithvi-600m-tl+terramind-large-v1")
GPU_MODEL       = os.getenv("GPU_MODEL", "unknown")
SAM3_TEXT_THR   = float(os.getenv("SAM3_TEXT_THRESHOLD", "0.30"))
SAM3_BOX_THR    = float(os.getenv("SAM3_BOX_THRESHOLD", "0.25"))
SAM3_IMAGE_SIZE = int(os.getenv("SAM3_IMAGE_SIZE", "1008"))
SAM3_PRITHVI_OVERLAY_THR = float(os.getenv("SAM3_PRITHVI_OVERLAY_THRESHOLD", "0.30"))
SAM3_SAR_CONF_CAP        = float(os.getenv("SAM3_SAR_CONF_CAP", "0.85"))
SAM3_MAX_PROMPTS         = int(os.getenv("SAM3_MAX_PROMPTS_PER_REQUEST", "1024"))
SAM3_OBB_OPENING_KERNEL_PCT = float(os.getenv("SAM3_OBB_OPENING_KERNEL_PCT", "0.01"))
SAM3_OBB_MIN_AREA_PX        = int(os.getenv("SAM3_OBB_MIN_AREA_PX", "4"))

# ── Lazy global pool (mirrors inference-sam2/main.py:56-216) ──
_pool: list[dict[str, Any]] = []
_pool_lock = threading.Lock()
_pool_idx  = 0
_load_lock = threading.Lock()
_model_error: str | None = None

def _load_pool() -> None:
    """Per-GPU model bundle. Reuses the resolve_devices/_auto_cuda_devices logic
    copied verbatim from inference-sam2/main.py — same env vars (DEVICE,
    CUDA_UNSUPPORTED_ARCH_POLICY)."""
    global _pool, _model_error
    if _pool: return
    with _load_lock:
        if _pool: return
        try:
            for device in sam3_runner.resolve_devices(os.getenv("DEVICE", "auto")):
                bundle = {
                    "device": device,
                    "lock":   threading.Lock(),
                    "sam3_image": sam3_runner.build_image(device),
                    "sam3_video": sam3_runner.build_video(device),
                    "dinov3_sat": embedding.load_sat(device),
                    "dinov3_lvd": embedding.load_lvd(device),
                    "prithvi":    prithvi_heads.load_all(device),
                    "terramind":  terramind.load(device),
                }
                _pool.append(bundle)
        except Exception as exc:
            _model_error = str(exc)

def _next_bundle() -> dict[str, Any]:
    if not _pool: _load_pool()
    if not _pool: raise HTTPException(503, f"Models not loaded: {_model_error}")
    global _pool_idx
    with _pool_lock:
        bundle = _pool[_pool_idx % len(_pool)]
        _pool_idx += 1
    return bundle

# ── /health ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": bool(_pool),
        "model_error": _model_error,
        "device": os.getenv("DEVICE", "auto"),
        "replicas": [{"device": b["device"]} for b in _pool],
        "model_versions": sam3_runner.versions(),
        "model_version": MODEL_VERSION,
        "gpu_model": GPU_MODEL,
    }

# ── /detect (image / chip) ─────────────────────────────────────────────────────
@app.post("/detect")
async def detect(image: UploadFile = File(...), metadata: str = Form("{}")):
    try: meta = json.loads(metadata)
    except json.JSONDecodeError: meta = {}
    raw   = await image.read()
    modality = meta.get("modality", "rgb").lower()
    bundle   = _next_bundle()

    if modality == "multispectral":
        chip6 = await run_in_threadpool(multispectral.decode_hls6, raw)
        chip3 = multispectral.hls_to_rgb_preview(chip6)
        # Crop classifier requires 3 temporally-aligned HLS scenes for the same
        # footprint — see §6.6 and the multicrop.yaml config (n_timesteps: 3).
        chip6_temporal_3 = (
            await run_in_threadpool(multispectral.decode_hls6_temporal_3, raw)
            if meta.get("hls_timesteps") == 3 else None
        )
    elif modality == "sar":
        chip2 = await run_in_threadpool(sar.decode_s1grd, raw)
        chip3 = await run_in_threadpool(
            terramind.s1_to_s2_rgb, bundle["terramind"], chip2, chip2.shape[-2:],
        )
        chip6 = chip6_temporal_3 = None
    else:                                    # rgb
        chip3 = await run_in_threadpool(_decode_rgb, raw)
        chip6 = chip6_temporal_3 = chip2 = None

    H, W = chip3.shape[:2]
    prompt_boxes = meta.get("prompt_boxes")
    if isinstance(prompt_boxes, list) and prompt_boxes:               # Mode B (grounded)
        candidates = await run_in_threadpool(
            sam3_runner.run_box_prompts, bundle, chip3, prompt_boxes, SAM3_BOX_THR,
        )
    else:                                                              # Mode A (text)
        prompts = resolve_prompts(meta, max_prompts=SAM3_MAX_PROMPTS)
        candidates = await run_in_threadpool(
            sam3_runner.run_text_prompts, bundle, chip3, prompts, SAM3_TEXT_THR,
        )

    # Modality-specific overlays / embeddings
    overlays = {}
    if modality == "multispectral":
        overlays = await run_in_threadpool(
            prithvi_heads.run_all, bundle["prithvi"], chip6, (H, W), chip6_temporal_3,
        )

    # Build detection records — open-vocab: no min-box filter, every candidate survives.
    detections = []
    for mask, bbox_xyxy, score, label in candidates:
        det = fusion.candidate_to_detection(
            mask, bbox_xyxy, score, label, image_size=(W, H), modality=modality,
        )
        if meta.get("geo"):
            det["geo"] = {**meta["geo"], "obb_map_crs": None, "obb_map_geojson": None}
        # DINOv3 embedding (SAT for satellite, LVD will be used in /detect_video)
        det["embedding"] = embedding.embed_crop(bundle["dinov3_sat"], chip3, bbox_xyxy)
        if modality == "multispectral":
            det["prithvi_labels"] = fusion.overlay_labels(mask, overlays, threshold=SAM3_PRITHVI_OVERLAY_THR)
        if modality == "sar":
            det["confidence"] = float(min(det["confidence"], SAM3_SAR_CONF_CAP))
            det["sar_proxy"] = True
            det["review_status"] = "review_candidate"
            det["terramind_embedding"] = terramind.pool_patches(bundle["terramind"], chip2)
        detections.append(det)

    detections = fusion.mask_aware_nms(detections, iou=0.50)
    return {
        "status": "success",
        "modality": modality,
        "detections": detections,
        "model_version": MODEL_VERSION,
        "model_versions": sam3_runner.versions(),
        "input_metadata": meta,
    }

def _decode_rgb(raw: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(raw))
    if img.mode != "RGB": img = img.convert("RGB")
    return np.array(img)

# ── /detect_video (FMV) ────────────────────────────────────────────────────────
@app.post("/detect_video")
async def detect_video(
    video: UploadFile | None = File(None),
    metadata: str = Form("{}"),
):
    meta = json.loads(metadata or "{}")
    bundle = _next_bundle()
    # Resolve video path: either uploaded file or shared volume reference.
    if video is not None:
        tmp = Path("/tmp") / f"{int(time.time()*1000)}_{video.filename}"
        with tmp.open("wb") as fh: fh.write(await video.read())
        video_path = str(tmp)
        cleanup_path: Path | None = tmp
    else:
        video_path = meta.get("video_path")
        if not video_path: raise HTTPException(400, "video upload or metadata.video_path required")
        cleanup_path = None

    prompts = resolve_prompts(meta, max_prompts=SAM3_MAX_PROMPTS)
    if not prompts: raise HTTPException(400, "No text_prompts supplied for FMV")
    frame_stride = max(1, int(meta.get("frame_stride", 1)))
    start_frame  = int(meta.get("start_frame", 0))
    end_frame    = meta.get("end_frame")  # None = run to end
    max_frames   = meta.get("max_frames")

    def stream():
        for ndjson_line in sam3_runner.run_video(
            bundle, video_path, prompts,
            frame_stride=frame_stride, start_frame=start_frame,
            end_frame=end_frame, max_frames=max_frames,
            dinov3=bundle["dinov3_lvd"],   # FMV is ground/oblique → LVD beats SAT
            score_threshold=SAM3_TEXT_THR,
        ):
            yield ndjson_line + "\n"
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)

    return StreamingResponse(stream(), media_type="application/x-ndjson")
```

### 6.2 `sam3_runner.py`

```python
"""SAM3 image + video model loaders and inference helpers.

Image: native facebookresearch/sam3 — build_sam3_image_model() + Sam3Processor.
       Text prompts via processor.set_text_prompt(); box prompts via
       processor.add_geometric_prompt(box=[cx,cy,w,h normalized], label=True).
       The per-image state caches backbone features so per-prompt cost is
       encoder-free. SAM 3.1 has no standalone image checkpoint — the image
       branch stays on facebook/sam3.
Video: build_sam3_multiplex_video_predictor() — SAM 3.1 Object Multiplex
       tracker. Plain SAM 3 path uses build_sam3_video_predictor().
       Both video builders take NO model_id parameter — the HF checkpoint
       is downloaded internally.
"""

import os, json
import numpy as np
import torch
from PIL import Image
from sam3.model_builder import (
    build_sam3_image_model,
    build_sam3_video_predictor,
    build_sam3_multiplex_video_predictor,
)
from sam3.model.sam3_image_processor import Sam3Processor

SAM3_IMAGE_MODEL_ID = os.getenv("SAM3_IMAGE_MODEL_ID", "facebook/sam3")  # informational; native loader uses upstream defaults
SAM3_USE_MULTIPLEX  = os.getenv("SAM3_USE_MULTIPLEX", "1") == "1"        # SAM 3.1 Object Multiplex for video
PROMPT_TEMPLATE     = os.getenv("SAM3_PROMPT_TEMPLATE", "{label}")       # upstream uses short noun phrases

def resolve_devices(value: str) -> list[str]:
    """Verbatim from inference-sam2/main.py:67-122 — unchanged."""
    ...

def build_image(device: str):
    """Load SAM3 image model + processor via the native upstream API."""
    model = build_sam3_image_model().to(device).eval()
    return {"model": model, "processor": Sam3Processor(model, device=device)}

def build_video(device: str):
    """Load the SAM3 video predictor.

    The native repo builders take no checkpoint_path / model_id arguments —
    the HF checkpoint is downloaded internally. SAM 3.1 Object Multiplex
    is the joint-multi-object tracker used at inference time.
    """
    if SAM3_USE_MULTIPLEX:
        return build_sam3_multiplex_video_predictor()      # SAM 3.1
    return build_sam3_video_predictor()                    # plain SAM 3

def versions() -> dict[str, str]:
    return {
        "sam3_image":    SAM3_IMAGE_MODEL_ID,
        "sam3_video":    "sam3.1-multiplex" if SAM3_USE_MULTIPLEX else "sam3",
        "dinov3_sat":    os.getenv("DINOV3_SAT_MODEL_ID", "facebook/dinov3-vitl16-pretrain-sat493m"),
        "dinov3_lvd":    os.getenv("DINOV3_LVD_MODEL_ID", "facebook/dinov3-vitl16-pretrain-lvd1689m"),
        "prithvi_backbone": os.getenv("PRITHVI_BACKBONE_ID", "ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL"),
        "prithvi_flood":    "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11",
        "prithvi_burn":     "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars",
        "prithvi_crop":     "ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification",
        "terramind":        os.getenv("TERRAMIND_MODEL_ID", "terramind_v1_large"),
    }

def run_text_prompts(bundle, image_rgb_uint8, prompts, score_threshold):
    """Mode A — loop text prompts → SAM3 → (mask, xyxy, score, label) candidates.

    Uses the native `Sam3Processor` state cache so the chip is encoded once
    and re-used across every prompt.
    """
    processor = bundle["sam3_image"]["processor"]
    pil_image = Image.fromarray(image_rgb_uint8)
    candidates = []

    with bundle["lock"], torch.inference_mode():
        state = processor.set_image(pil_image)             # caches backbone features
        for label in prompts:
            phrase = PROMPT_TEMPLATE.format(label=label)
            out = processor.set_text_prompt(prompt=phrase, state=state)
            for mask, box_xyxy, score in zip(out["masks"], out["boxes"], out["scores"]):
                if float(score) < score_threshold:
                    continue
                mask_np   = mask.cpu().numpy().astype(np.bool_)
                bbox_xyxy = [float(v) for v in box_xyxy.cpu().numpy()]   # absolute pixel xyxy
                candidates.append((mask_np, bbox_xyxy, float(score), label))
    return candidates

def _to_xyxy_pixels(entry, width: int, height: int) -> list[float] | None:
    """Convert a normalized prompt entry from worker.py into [x1,y1,x2,y2] pixel xyxy.

    Accepts either entry["bbox"] = [cx,cy,w,h] in [0,1] or
    entry["obb"]  = [x1,y1,...,x4,y4] in [0,1]. Returns None if degenerate.
    """
    bbox = entry.get("bbox"); obb = entry.get("obb")
    if obb and len(obb) >= 8:
        xs = [float(obb[i]) for i in range(0, 8, 2)]
        ys = [float(obb[i]) for i in range(1, 8, 2)]
        x1n, y1n, x2n, y2n = min(xs), min(ys), max(xs), max(ys)
    elif bbox and len(bbox) >= 4:
        cx, cy, w, h = (float(v) for v in bbox[:4])
        x1n, y1n, x2n, y2n = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    else:
        return None
    x1 = max(0.0, min(float(width),  x1n * width))
    y1 = max(0.0, min(float(height), y1n * height))
    x2 = max(0.0, min(float(width),  x2n * width))
    y2 = max(0.0, min(float(height), y2n * height))
    return [x1, y1, x2, y2] if (x2 - x1 >= 1.0 and y2 - y1 >= 1.0) else None

def run_box_prompts(bundle, image_rgb_uint8, prompt_boxes, score_threshold):
    """Mode B — SAM2-compatible. Caller-supplied boxes become positive visual
    prompts via the transformers `input_boxes` / `input_boxes_labels` route.
    Label conventions: 1 = positive, 0 = negative, -10 = padding
    (https://huggingface.co/docs/transformers/main/en/model_doc/sam3
    §"Prompt Label Conventions").
    """
    model     = bundle["sam3_image"]["model"]
    processor = bundle["sam3_image"]["processor"]
    pil_image = Image.fromarray(image_rgb_uint8)
    h, w = image_rgb_uint8.shape[:2]
    candidates = []

    with bundle["lock"], torch.inference_mode():
        for entry in prompt_boxes:
            box_xyxy = _to_xyxy_pixels(entry, w, h)
            if box_xyxy is None: continue
            label = entry.get("class") or entry.get("original_class") or "segment"
            inputs = processor(
                images=pil_image,
                input_boxes=[[box_xyxy]],            # [batch, num_boxes, 4] xyxy pixel coords
                input_boxes_labels=[[1]],            # 1 = positive
                return_tensors="pt",
            ).to(model.device)
            outputs = model(**inputs)
            results = processor.post_process_instance_segmentation(
                outputs,
                threshold=score_threshold,
                mask_threshold=0.5,
                target_sizes=inputs.get("original_sizes").tolist(),
            )[0]
            for mask, b, s in zip(results["masks"], results["boxes"], results["scores"]):
                mask_np   = mask.cpu().numpy().astype(np.bool_)
                bbox_xyxy = [float(v) for v in b.cpu().numpy()]
                candidates.append((mask_np, bbox_xyxy, float(s), label))
    return candidates

def run_video(bundle, video_path, prompts, *, frame_stride, start_frame,
              end_frame, max_frames, dinov3, score_threshold):
    """Generator yielding NDJSON strings — one per frame×track detection."""
    predictor = bundle["sam3_video"]
    s = predictor.handle_request(request={"type": "start_session", "resource_path": video_path})
    session_id = s["session_id"]

    # Add one prompt per phrase. Object Multiplex (SAM 3.1) groups them into shared memory.
    for prompt in prompts:
        predictor.handle_request(request={
            "type": "add_prompt", "session_id": session_id,
            "frame_index": start_frame, "text": prompt,
        })

    # Propagate. The official SAM3.1 notebook uses handle_stream_request with
    # type="propagate_in_video"; _iter_sam3_video_tracks adapts that output to
    # our storage schema.
    for resp in predictor.handle_stream_request(request={
        "type": "propagate_in_video", "session_id": session_id,
    }):
        frame_idx = resp["frame_index"]
        if frame_idx < start_frame: continue
        if end_frame is not None and frame_idx > end_frame: break
        if (frame_idx - start_frame) % frame_stride: continue
        if max_frames is not None and frame_idx >= start_frame + max_frames * frame_stride: break
        for track in _iter_sam3_video_tracks(resp["outputs"]):
            mask = track["mask"]
            x1, y1, x2, y2 = track["bbox_xyxy"]
            score = float(track["score"])
            if score < score_threshold: continue                # only floor is the SAM3 score
            import fusion
            mh, mw = mask.shape[-2:]
            obb = fusion.mask_to_obb_record(mask, [x1, y1, x2, y2], mw, mh)
            entry = {
                "frame_index": frame_idx,
                "track_id": int(track["track_id"]),
                "class": track["prompt_text"],
                "original_class": track["prompt_text"],
                "parent_class": "track",
                "bbox_xyxy": [x1, y1, x2, y2],
                "obb": obb["points"],
                "obb_format": "yolo_obb_normalized_xyxyxyxy",
                "obb_source": obb["source"],
                "obb_angle_deg": obb["angle_deg"],
                "edge_truncated": obb["edge_truncated"],
                "score": score,
                "mask_rle": _coco_rle(mask),
            }
            if track.get("first_seen", False):
                # Embed once per track on its first frame to avoid 30 FPS × N tracks DINO calls.
                crop = _crop_frame(_read_frame_rgb(video_path, frame_idx), (x1, y1, x2, y2))
                entry["embedding"] = embedding.dinov3_pool(dinov3, crop)
            yield json.dumps(entry, separators=(",", ":"))

    predictor.handle_request(request={"type": "close_session", "session_id": session_id})

def _iter_sam3_video_tracks(outputs):
    """Normalize SAM3.1 per-frame outputs into {track_id, mask, bbox, score, prompt_text}.

    The exact tensor/dict shape should be locked to the pinned SAM3 commit; the
    official API exposes outputs per frame through handle_stream_request.
    """
    ...

def _read_frame_rgb(video_path: str, frame_idx: int) -> np.ndarray:
    ...
```

### 6.3 `multispectral.py`

```python
"""HLS-6 / Sentinel-2-L2A 6-band decoder, normalizer, and RGB preview.

Bands in the order Prithvi expects:
  Blue (B02) → Green (B03) → Red (B04) → Narrow-NIR (B8A) → SWIR-1 (B11) → SWIR-2 (B12).

Reference: https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11
"""
import io
import numpy as np
import rasterio

PRITHVI_CONSTANT_SCALE = 0.0001     # from sen1floods11.yaml constant_scale

def decode_hls6(payload: bytes) -> np.ndarray:
    """Decode 6-band GeoTIFF chip → (6,H,W) float32 in reflectance scale."""
    with rasterio.open(io.BytesIO(payload)) as src:
        if src.count < 6:
            raise ValueError(f"Expected ≥6 HLS bands, got {src.count}")
        arr = src.read(indexes=range(1, 7)).astype(np.float32)
    return arr * PRITHVI_CONSTANT_SCALE if np.nanmean(arr) > 1.0 else arr

def decode_hls6_temporal_3(payload: bytes) -> np.ndarray | None:
    """Decode an optional 3-timestep HLS stack → (6, 3, H, W), else None.

    The multi-temporal-crop classifier is documented (and configured in
    NASA-IMPACT/Prithvi-EO-2.0/configs/multicrop.yaml — `n_timesteps: 3`)
    to require 6 bands × 3 timesteps. Bands 1-6 are timestep t0,
    bands 7-12 are t1, bands 13-18 are t2 — same band ordering as the
    single-timestep case.
    """
    with rasterio.open(io.BytesIO(payload)) as src:
        if src.count < 18:
            return None
        flat = src.read(indexes=range(1, 19)).astype(np.float32)        # (18,H,W)
    if np.nanmean(flat) > 1.0:
        flat = flat * PRITHVI_CONSTANT_SCALE
    h, w = flat.shape[-2:]
    return flat.reshape(3, 6, h, w).transpose(1, 0, 2, 3)               # (6, 3, H, W)

def hls_to_rgb_preview(arr_reflectance: np.ndarray) -> np.ndarray:
    """(6,H,W) float reflectance → (H,W,3) uint8 — 2-98 % stretch on R,G,B (bands 2,1,0)."""
    rgb = arr_reflectance[[2, 1, 0]]
    p2, p98 = np.percentile(rgb, [2, 98], axis=(1, 2), keepdims=True)
    rgb = np.clip((rgb - p2) / np.maximum(p98 - p2, 1e-6), 0.0, 1.0)
    return (rgb * 255).astype(np.uint8).transpose(1, 2, 0)

def pad_to_window(arr_reflectance: np.ndarray, window_size: int = 512) -> np.ndarray:
    """Reflect-pad CHW input to a multiple of the released head window size."""
    h, w = arr_reflectance.shape[-2:]
    pad_h = (window_size - (h % window_size)) % window_size
    pad_w = (window_size - (w % window_size)) % window_size
    return np.pad(arr_reflectance, ((0, 0), (0, pad_h), (0, pad_w)), mode="reflect")
```

### 6.4 `sar.py`

```python
"""Sentinel-1 GRD VV/VH 2-band decoder.

Input: float32 GeoTIFF in dB or linear amplitude.
Decoder output stays at native chip resolution in [0,1]; resize_to_terramind
creates the TerraMind-ready (2,224,224) tensor.
"""
import io
import numpy as np
import rasterio

TERRAMIND_S1_SIZE = 224
SAR_DB_FLOOR = -30.0
SAR_DB_CEIL  =   0.0

def decode_s1grd(payload: bytes) -> np.ndarray:
    """Decode 2-band SAR chip → (2,H,W) float32 in [0,1] linear stretch from dB clip."""
    with rasterio.open(io.BytesIO(payload)) as src:
        if src.count < 2:
            raise ValueError(f"Expected 2 SAR bands (VV,VH), got {src.count}")
        arr = src.read(indexes=[1, 2]).astype(np.float32)
    if np.nanmin(arr) >= 0:                      # linear amplitude → convert
        arr = 10.0 * np.log10(np.maximum(arr, 1e-6))
    arr = np.clip(arr, SAR_DB_FLOOR, SAR_DB_CEIL)
    arr = (arr - SAR_DB_FLOOR) / (SAR_DB_CEIL - SAR_DB_FLOOR)
    return arr.astype(np.float32)

def resize_to_terramind(arr_norm: np.ndarray) -> np.ndarray:
    import cv2
    chw = arr_norm.transpose(1, 2, 0)
    chw = cv2.resize(chw, (TERRAMIND_S1_SIZE, TERRAMIND_S1_SIZE),
                     interpolation=cv2.INTER_LINEAR)
    return chw.transpose(2, 0, 1).astype(np.float32)
```

### 6.5 `terramind.py`

```python
"""TerraMind v1 large — SAR backbone + S1GRD→S2L2A→RGB preview.

Loaded via TerraTorch BACKBONE_REGISTRY / FULL_MODEL_REGISTRY.
Reference: https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-large
"""
import os
import numpy as np
import torch
from terratorch import BACKBONE_REGISTRY, FULL_MODEL_REGISTRY

TERRAMIND_MODEL_ID = os.getenv("TERRAMIND_MODEL_ID", "terramind_v1_large")

def load(device: str):
    backbone = BACKBONE_REGISTRY.build(
        TERRAMIND_MODEL_ID, pretrained=True, modalities=["S1GRD"]
    ).to(device).eval()
    generator = FULL_MODEL_REGISTRY.build(
        f"{TERRAMIND_MODEL_ID}_generate",
        pretrained=True,
        modalities=["S1GRD"],
        output_modalities=["S2L2A"],
        timesteps=10,
        standardize=True,
    ).to(device).eval()
    return {"backbone": backbone, "generator": generator, "device": device}

def s1_to_s2_rgb(bundle, chip2_norm: np.ndarray,
                 target_hw: tuple[int, int] | None = None) -> np.ndarray:
    """Generate an S2L2A proxy from S1 GRD and render TerraTorch's documented
    S2L2A Red/Green/Blue bands as an RGB preview."""
    import sar as _sar
    arr224 = _sar.resize_to_terramind(chip2_norm)
    with torch.inference_mode():
        x = torch.from_numpy(arr224).unsqueeze(0).to(bundle["device"])
        generated = bundle["generator"]({"S1GRD": x})
    s2 = generated["S2L2A"].squeeze(0).detach().cpu().numpy()  # (12,224,224)
    rgb = s2[[3, 2, 1]]  # S2L2A order: coastal, blue, green, red, ...
    p2, p98 = np.percentile(rgb, [2, 98], axis=(1, 2), keepdims=True)
    rgb = np.clip((rgb - p2) / np.maximum(p98 - p2, 1e-6), 0.0, 1.0)
    preview = (rgb * 255).astype(np.uint8).transpose(1, 2, 0)
    if target_hw is not None and preview.shape[:2] != tuple(target_hw):
        import cv2
        preview = cv2.resize(preview, (target_hw[1], target_hw[0]),
                             interpolation=cv2.INTER_LINEAR)
    return preview

def pool_patches(bundle, chip2_norm: np.ndarray) -> dict:
    """Return mean-pooled 768-d embedding (mean over 196 patch tokens)."""
    import sar as _sar
    arr224 = _sar.resize_to_terramind(chip2_norm)
    with torch.inference_mode():
        x = torch.from_numpy(arr224).unsqueeze(0).to(bundle["device"])
        out = bundle["backbone"]({"S1GRD": x})    # list; final tensor (1,196,768)
    tokens = out[-1] if isinstance(out, list) else out
    vec = tokens.mean(dim=1).squeeze(0).to(torch.float16).cpu().numpy()
    import base64
    return {"model": TERRAMIND_MODEL_ID, "dim": int(vec.shape[0]),
            "fp16_b64": base64.b64encode(vec.tobytes()).decode("ascii")}
```

### 6.6 `prithvi_heads.py`

```python
"""Prithvi-EO published downstream heads — loaded via terratorch BACKBONE_REGISTRY.

Each HF model card explicitly recommends:
    from terratorch.registry import BACKBONE_REGISTRY
    model = BACKBONE_REGISTRY.build("<HF id>")

Note the input requirements:
* `Prithvi-EO-2.0-300M-TL-Sen1Floods11`  — 6-band S2 (Blue, Green, Red, Narrow-NIR,
  SWIR-1, SWIR-2), single timestamp. 3-class output: 0=no-water, 1=water/flood, -1=no-data.
  Source: https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11
* `Prithvi-EO-2.0-300M-BurnScars`        — same 6 bands, single timestamp. Binary output.
  Source: https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars
* `Prithvi-EO-1.0-100M-multi-temporal-crop-classification` — REQUIRES **3 TIMESTEPS** of the
  same 6 bands (input shape (1, 6, 3, H, W)), 13 CDL crop classes per pixel.
  Source: https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification
  Confirmed by configs/multicrop.yaml (n_timesteps: 3) in NASA-IMPACT/Prithvi-EO-2.0.
"""
import os
import numpy as np
import torch
from terratorch.registry import BACKBONE_REGISTRY

PRITHVI_FLOOD_ID = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11"
PRITHVI_BURN_ID  = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars"
PRITHVI_CROP_ID  = "ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification"

# 13 crop / land-cover classes — order verified against the HF model card results table:
# https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification
CROP_CLASS_NAMES = [
    "natural_vegetation", "forest", "corn", "soybeans", "wetlands",
    "developed_barren", "open_water", "winter_wheat", "alfalfa",
    "fallow_idle_cropland", "cotton", "sorghum", "other",
]

def load_all(device: str):
    return {
        "flood":  BACKBONE_REGISTRY.build(PRITHVI_FLOOD_ID).to(device).eval(),
        "burn":   BACKBONE_REGISTRY.build(PRITHVI_BURN_ID).to(device).eval(),
        "crop":   BACKBONE_REGISTRY.build(PRITHVI_CROP_ID).to(device).eval(),
        "device": device,
    }

def run_all(prithvi_bundle, chip6_full: np.ndarray, target_hw: tuple[int, int],
            chip6_temporal_3: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Run flood + burn (single-timestamp) and optionally crop (3 timesteps).

    Args:
      chip6_full:        (6, H, W) float32 reflectance — single timestamp.
      chip6_temporal_3:  (6, 3, H, W) optional float32 reflectance — 3 timestamps,
                         supplied by the worker only when 3 temporally-aligned HLS
                         scenes are available for the chip footprint. When None,
                         crop classification is skipped (per the HF card's
                         3-timestep requirement; faking a temporal stack gives
                         unreliable outputs).
      target_hw:         (H, W) of the original chip; overlays are upsampled here.
    """
    import multispectral, cv2
    H, W = target_hw
    overlays: dict[str, np.ndarray] = {}
    device = prithvi_bundle["device"]

    chip6_224 = multispectral.resize_to_prithvi(chip6_full)             # (6,224,224)
    x = torch.from_numpy(chip6_224).unsqueeze(0).to(device)             # (1,6,224,224)

    with torch.inference_mode():
        flood_logits = prithvi_bundle["flood"](x)                        # (1,3,224,224)
        flood_mask   = (flood_logits.argmax(1)[0].cpu().numpy() == 1)
        overlays["water"] = cv2.resize(flood_mask.astype(np.uint8), (W, H),
                                        interpolation=cv2.INTER_NEAREST).astype(bool)

        burn_logits = prithvi_bundle["burn"](x)                          # (1,2,224,224)
        burn_mask   = (burn_logits.argmax(1)[0].cpu().numpy() == 1)
        overlays["burn_scar"] = cv2.resize(burn_mask.astype(np.uint8), (W, H),
                                            interpolation=cv2.INTER_NEAREST).astype(bool)

        if chip6_temporal_3 is not None:
            chip3t_224 = np.stack([
                multispectral.resize_to_prithvi(chip6_temporal_3[:, t]) for t in range(3)
            ], axis=1)                                                   # (6,3,224,224)
            xt = torch.from_numpy(chip3t_224).unsqueeze(0).to(device)    # (1,6,3,224,224)
            crop_logits = prithvi_bundle["crop"](xt)                     # (1,13,224,224)
            crop_map    = crop_logits.argmax(1)[0].cpu().numpy().astype(np.int16)
            overlays["crop"] = cv2.resize(crop_map, (W, H), interpolation=cv2.INTER_NEAREST)

    return overlays

def crop_class_name(label_map: np.ndarray, bbox_xyxy: list[float]) -> str:
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_xyxy)
    region = label_map[max(0, y1):y2, max(0, x1):x2]
    if region.size == 0: return "unknown"
    cls_id = int(np.bincount(region.flatten().astype(np.int64)).argmax())
    return CROP_CLASS_NAMES[cls_id] if 0 <= cls_id < len(CROP_CLASS_NAMES) else "unknown"
```

### 6.7 `embedding.py`

```python
"""DINOv3 embedder — dual model (SAT for satellite, LVD for FMV).

Reference: https://huggingface.co/facebook/dinov3-vitl16-pretrain-sat493m
           https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m
"""
import base64
import os
import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel

DINOV3_SAT_MODEL_ID = os.getenv("DINOV3_SAT_MODEL_ID", "facebook/dinov3-vitl16-pretrain-sat493m")
DINOV3_LVD_MODEL_ID = os.getenv("DINOV3_LVD_MODEL_ID", "facebook/dinov3-vitl16-pretrain-lvd1689m")

def _load(model_id: str, device: str):
    return {
        "model_id": model_id,
        "processor": AutoImageProcessor.from_pretrained(model_id),
        "model":     AutoModel.from_pretrained(model_id, torch_dtype=torch.float16).to(device).eval(),
        "device":    device,
    }

def load_sat(device: str): return _load(DINOV3_SAT_MODEL_ID, device)
def load_lvd(device: str): return _load(DINOV3_LVD_MODEL_ID, device)

def embed_crop(bundle, image_uint8: np.ndarray, bbox_xyxy: list[float]) -> dict:
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_xyxy)
    H, W = image_uint8.shape[:2]
    x1 = max(0, x1); y1 = max(0, y1); x2 = min(W, x2); y2 = min(H, y2)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return {"model": bundle["model_id"], "dim": 0, "fp16_b64": ""}
    crop = Image.fromarray(image_uint8[y1:y2, x1:x2])
    return dinov3_pool(bundle, crop)

def dinov3_pool(bundle, pil_image_or_array) -> dict:
    if isinstance(pil_image_or_array, np.ndarray):
        pil_image_or_array = Image.fromarray(pil_image_or_array)
    inp = bundle["processor"](images=pil_image_or_array, return_tensors="pt").to(bundle["device"])
    with torch.inference_mode():
        out = bundle["model"](**inp)
    vec = out.last_hidden_state[:, 0, :].squeeze(0).to(torch.float16).cpu().numpy()
    return {
        "model":   bundle["model_id"],
        "dim":     int(vec.shape[0]),
        "fp16_b64": base64.b64encode(vec.tobytes()).decode("ascii"),
    }
```

### 6.8 `prompts/loader.py`

```python
import json, os
from pathlib import Path

PROMPTS_DIR     = Path(__file__).parent
EXTRA_FILE      = os.getenv("SAM3_LABEL_FILE")        # optional override file
FORCED_PROFILE  = os.getenv("SAM3_DEFAULT_PROMPT_PROFILE")  # if set, overrides modality auto-select

def _normalize(label: str) -> str:
    return " ".join(label.strip().lower().split())

def _load_profile(name: str) -> list[str]:
    path = PROMPTS_DIR / f"{name}.json"
    if not path.exists():
        raise ValueError(f"Unknown prompt profile {name!r}")
    return json.loads(path.read_text())["prompts"]

def select_default_profile(modality: str) -> str:
    """Auto-select per modality. FMV → ground vocab; everything else → satellite vocab."""
    return "ground_v1" if modality == "fmv" else "satellite_v1"

def resolve_prompts(meta: dict, *, max_prompts: int) -> list[str]:
    if isinstance(meta.get("text_prompts"), list) and meta["text_prompts"]:
        prompts = meta["text_prompts"]
    elif EXTRA_FILE and Path(EXTRA_FILE).exists():
        prompts = json.loads(Path(EXTRA_FILE).read_text())["prompts"]
    else:
        modality = (meta.get("modality") or "rgb").lower()
        profile  = meta.get("prompt_profile") or FORCED_PROFILE or select_default_profile(modality)
        prompts  = _load_profile(profile)

    seen, out = set(), []
    for raw in prompts:
        n = _normalize(str(raw))
        if not n or n in seen: continue
        seen.add(n); out.append(n)
        if len(out) >= max_prompts: break
    if not out:
        raise ValueError("No labels supplied for SAM3")
    return out
```

### 6.9 `fusion.py` — output shaping

```python
import os
import base64
import numpy as np
import cv2
from pycocotools import mask as coco_mask
from detection_policy import parent_class_for_label   # reuse backend helper via shared volume

OBB_OPENING_KERNEL_PCT = float(os.getenv("SAM3_OBB_OPENING_KERNEL_PCT", "0.01"))
OBB_MIN_AREA_PX = int(os.getenv("SAM3_OBB_MIN_AREA_PX", "4"))

def candidate_to_detection(mask_bool, bbox_xyxy, score, label, *, image_size,
                           modality, valid_mask=None):
    W, H = image_size
    x1, y1, x2, y2 = bbox_xyxy
    parent = parent_class_for_label(label)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    obb = mask_to_obb_record(mask_bool, bbox_xyxy, W, H, valid_mask=valid_mask)
    return {
        "class": parent,
        "original_class": label,
        "parent_class": parent,
        "bbox": [
            max(0.0, min(1.0, cx / W)),
            max(0.0, min(1.0, cy / H)),
            max(0.0, min(1.0, (x2 - x1) / W)),
            max(0.0, min(1.0, (y2 - y1) / H)),
        ],
        "obb": obb["points"],
        "obb_format": "yolo_obb_normalized_xyxyxyxy",
        "obb_source": obb["source"],
        "obb_angle_deg": obb["angle_deg"],
        "obb_area_px": obb["area_px"],
        "edge_truncated": obb["edge_truncated"],
        "confidence": float(score),
        "mask_rle": _coco_rle(mask_bool),
        "area": int(mask_bool.sum()),
        "modality": modality,
        "task": "sam3_open_vocab_object_detection",
    }

def mask_to_obb_record(mask_bool, bbox_xyxy, W: int, H: int, *, valid_mask=None) -> dict:
    return _mask_to_obb(mask_bool, W, H, fallback_bbox_xyxy=bbox_xyxy,
                        valid_mask=valid_mask)

def _coco_rle(mask_bool: np.ndarray) -> dict:
    rle = coco_mask.encode(np.asfortranarray(mask_bool.astype(np.uint8)))
    rle["counts"] = base64.b64encode(rle["counts"]).decode("ascii")
    return rle

def _mask_to_obb(mask_bool, W, H, *, fallback_bbox_xyxy, valid_mask=None):
    """Mask -> normalized 8-corner OBB via contour refinement + minAreaRect.

    This keeps the HBB2OBB idea local: no extra model, no training, no external
    annotation service. If geometry fails, return the HBB as an axis-aligned OBB.
    """
    work = np.asarray(mask_bool, dtype=bool)
    if valid_mask is not None:
        work = np.logical_and(work, np.asarray(valid_mask, dtype=bool))
    edge_truncated = _touches_edge(work, W, H)
    binary = work.astype(np.uint8)
    if binary.sum() == 0:
        return _hbb_fallback(fallback_bbox_xyxy, W, H, edge_truncated)

    ys, xs = np.where(binary)
    extent = max(1, min(int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)))
    k = int(round(extent * OBB_OPENING_KERNEL_PCT))
    if k >= 2:
        if k % 2 == 0: k += 1
        kernel = np.ones((k, k), np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return _hbb_fallback(fallback_bbox_xyxy, W, H, edge_truncated)
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    if area < OBB_MIN_AREA_PX:
        return _hbb_fallback(fallback_bbox_xyxy, W, H, edge_truncated)
    rect = cv2.minAreaRect(contour)
    pts  = cv2.boxPoints(rect)
    return {
        "points": _normalize_obb_points(pts, W, H),
        "source": "mask_min_area_rect",
        "angle_deg": float(rect[2]),
        "area_px": area,
        "edge_truncated": edge_truncated,
    }

def _normalize_obb_points(pts, W, H):
    flat = []
    for px, py in pts:
        flat.append(max(0.0, min(1.0, float(px) / W)))
        flat.append(max(0.0, min(1.0, float(py) / H)))
    return flat

def _hbb_fallback(bbox_xyxy, W, H, edge_truncated: bool):
    x1, y1, x2, y2 = bbox_xyxy
    pts = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)
    return {
        "points": _normalize_obb_points(pts, W, H),
        "source": "hbb_fallback",
        "angle_deg": 0.0,
        "area_px": float(max(0.0, x2 - x1) * max(0.0, y2 - y1)),
        "edge_truncated": edge_truncated,
    }

def _touches_edge(mask_bool, W, H) -> bool:
    if not mask_bool.any(): return False
    h, w = mask_bool.shape[-2:]
    return bool(mask_bool[0, :].any() or mask_bool[h-1, :].any() or
                mask_bool[:, 0].any() or mask_bool[:, w-1].any())

def overlay_labels(mask_bool, overlays, *, threshold):
    labels = []
    if "water" in overlays and _iou(mask_bool, overlays["water"]) >= threshold:
        labels.append("water")
    if "burn_scar" in overlays and _iou(mask_bool, overlays["burn_scar"]) >= threshold:
        labels.append("burn_scar")
    if "crop" in overlays:
        from prithvi_heads import crop_class_name
        ys, xs = np.where(mask_bool)
        if len(xs) > 0:
            x1, y1, x2, y2 = float(xs.min()), float(ys.min()), float(xs.max()+1), float(ys.max()+1)
            labels.append(f"crop:{crop_class_name(overlays['crop'], [x1,y1,x2,y2])}")
    return labels

def _iou(a_bool, b_bool):
    inter = np.logical_and(a_bool, b_bool).sum()
    union = np.logical_or(a_bool, b_bool).sum()
    return float(inter) / float(union) if union else 0.0

def mask_aware_nms(detections, iou=0.50):
    """Class-aware mask NMS — keeps highest-confidence per (class, IoU>th) cluster.
    Decode RLE → bool mask once, compute pairwise IoU greedily."""
    if not detections: return []
    ranked = sorted(detections, key=lambda d: float(d["confidence"]), reverse=True)
    masks = [coco_mask.decode({**d["mask_rle"], "counts": base64.b64decode(d["mask_rle"]["counts"])})
             .astype(bool) for d in ranked]
    keep = []
    suppressed = [False] * len(ranked)
    for i in range(len(ranked)):
        if suppressed[i]: continue
        keep.append(ranked[i])
        for j in range(i + 1, len(ranked)):
            if suppressed[j]: continue
            if ranked[i]["class"] != ranked[j]["class"]: continue
            if _iou(masks[i], masks[j]) >= iou: suppressed[j] = True
    return keep
```

### 6.10 `exports/obb.py` — offline OBB interchange

```python
"""OBB exporters. No training pipeline is invoked here; these are analysis and
interchange formats for downstream GIS/CV tools."""

def affine_from_geo_meta(geo: dict):
    from affine import Affine
    if geo.get("chip_transform_order") == "gdal":
        return Affine.from_gdal(*geo["chip_transform"])
    return Affine(*geo["chip_transform"])

def to_yolo_obb_line(class_index: int, obb_norm: list[float]) -> str:
    # Ultralytics YOLO OBB dataset format: class_index x1 y1 ... x4 y4,
    # all coordinates normalized to [0,1].
    vals = [str(class_index)] + [f"{v:.6f}" for v in obb_norm]
    return " ".join(vals)

def to_dota_line(label: str, obb_norm: list[float], width: int, height: int,
                 difficult: int = 0) -> str:
    pts = []
    for i, v in enumerate(obb_norm):
        scale = width if i % 2 == 0 else height
        pts.append(str(int(round(v * scale))))
    return " ".join(pts + [label, str(difficult)])

def to_geojson_feature(det: dict, affine_transform, image_size: tuple[int, int],
                       properties: dict | None = None) -> dict:
    # Fast review path: convert normalized pixel OBB corners through the actual
    # chip affine transform. For metric GIS labels, use mask_to_map_obb_feature.
    width, height = image_size
    obb = det["obb"]
    coords = []
    for x_norm, y_norm in zip(obb[0::2], obb[1::2]):
        x_img = x_norm * width
        y_img = y_norm * height
        x_geo, y_geo = affine_transform * (x_img, y_img)
        coords.append([x_geo, y_geo])
    coords.append(coords[0])
    props = {
        "class": det["class"],
        "original_class": det.get("original_class"),
        "confidence": det.get("confidence"),
        "provider": "sam3",
        **(properties or {}),
    }
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [coords]}}

def mask_to_map_obb_feature(mask, *, transform, src_crs, dst_crs=None,
                            properties: dict | None = None) -> dict | None:
    """Authoritative GIS path: mask -> polygon(s) -> projected OBB polygon.

    Rasterio polygonization applies the chip transform, so the geometry starts
    in map coordinates rather than pixel coordinates. If dst_crs is supplied,
    Shapely computes the minimum rectangle in that projected CRS. Use a local
    projected CRS when exporting metric width/height/angle.
    """
    from rasterio.features import shapes
    from shapely.geometry import shape, mapping
    from shapely.ops import transform as shp_transform, unary_union
    from pyproj import CRS, Transformer

    mask_bool = mask.astype("uint8")
    polys = []
    for geom, value in shapes(mask_bool, mask=mask_bool.astype(bool), transform=transform):
        if int(value) != 1:
            continue
        poly = shape(geom).buffer(0)
        if not poly.is_empty:
            polys.append(poly)
    if not polys:
        return None

    merged = unary_union(polys).buffer(0)
    work = merged
    export_crs = CRS.from_user_input(src_crs)
    if dst_crs:
        target = CRS.from_user_input(dst_crs)
        tx = Transformer.from_crs(export_crs, target, always_xy=True)
        work = shp_transform(tx.transform, merged)
        export_crs = target

    obb = work.minimum_rotated_rectangle
    if obb.geom_type != "Polygon":
        return None

    props = {"provider": "sam3", "crs": export_crs.to_string(), **(properties or {})}
    return {"type": "Feature", "properties": props, "geometry": mapping(obb)}

def postgis_oriented_envelope_sql(mask_table: str = "sam3_masks") -> str:
    return (
        f"SELECT id, ST_OrientedEnvelope(geom) AS obb_geom "
        f"FROM {mask_table} WHERE NOT ST_IsEmpty(geom);"
    )
```

---

## 7. Dockerfile.gpu

```dockerfile
ARG CUDA_VERSION=12.6.3
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git wget curl ffmpeg \
        libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 \
        python3.12 python3.12-dev python3.12-venv python3-pip \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && rm -rf /var/lib/apt/lists/*

# Torch first — pin to whatever scripts/configure_host.py emits, but require ≥2.7 for SAM3.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126
ARG TORCH_VERSION=2.7.1
ARG TORCHVISION_VERSION=0.22.1
RUN pip install --no-cache-dir \
        "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
        --index-url ${TORCH_INDEX_URL} --extra-index-url https://pypi.org/simple

ARG TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0;12.0+PTX"
ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}
ENV FORCE_CUDA=1
ENV HF_HOME=/models/hf \
    HUGGINGFACE_HUB_CACHE=/models/hf/hub \
    TORCH_HOME=/models/torch \
    XDG_CACHE_HOME=/models/cache

# SAM3 from source (no PyPI release yet)
RUN git clone --depth 1 https://github.com/facebookresearch/sam3.git /opt/sam3 \
    && cd /opt/sam3 && pip install -e .

# TerraTorch (handles Prithvi + TerraMind), Transformers (DINOv3), rasterio, etc.
RUN pip install --no-cache-dir \
        "transformers>=4.56,<5" \
        "terratorch>=1.1,<1.5" \
        "huggingface_hub>=0.24" \
        accelerate \
        rasterio \
        shapely pyproj affine \
        pycocotools \
        opencv-python-headless \
        pillow \
        fastapi uvicorn[standard] python-multipart requests

# Pre-pull weights at build time so the runtime is offline.
ARG HF_TOKEN
ARG SAM3_IMAGE_MODEL_ID=facebook/sam3.1
ARG SAM3_FALLBACK_IMAGE_MODEL_ID=facebook/sam3
ARG DINOV3_SAT_MODEL_ID=facebook/dinov3-vitl16-pretrain-sat493m
ARG DINOV3_LVD_MODEL_ID=facebook/dinov3-vitl16-pretrain-lvd1689m
ARG PRITHVI_BACKBONE_ID=ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL
ARG TERRAMIND_MODEL_ID=terramind_v1_large

RUN python - <<'PY'
import os, huggingface_hub as hh
tok = os.environ.get("HF_TOKEN") or None
if tok: hh.login(token=tok, add_to_git_credential=False)
# DINOv3 (gated)
from transformers import AutoModel, AutoImageProcessor
for mid in (os.environ["DINOV3_SAT_MODEL_ID"], os.environ["DINOV3_LVD_MODEL_ID"]):
    AutoImageProcessor.from_pretrained(mid)
    AutoModel.from_pretrained(mid)
# SAM3 image + SAM3.1 video checkpoints (gated). Snapshot downloads avoid
# requiring a GPU during docker build. We pull both `facebook/sam3` and
# `facebook/sam3.1` so the runtime can switch via SAM3_IMAGE_MODEL_ID without
# redownloading. The video predictor builders (build_sam3_video_predictor /
# build_sam3_multiplex_video_predictor) take no model_id and rely on the HF
# cache populated here.
for repo in (
    os.environ["SAM3_IMAGE_MODEL_ID"],
    os.environ.get("SAM3_FALLBACK_IMAGE_MODEL_ID", "facebook/sam3"),
):
    hh.snapshot_download(repo)
# Prithvi (apache-2.0, ungated)
import terratorch
from terratorch import BACKBONE_REGISTRY
BACKBONE_REGISTRY.build(os.environ["PRITHVI_BACKBONE_ID"], pretrained=True)
for repo in [
    "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11",
    "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars",
    "ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification",
]:
    hh.snapshot_download(repo)
# TerraMind (apache-2.0, ungated)
BACKBONE_REGISTRY.build(os.environ["TERRAMIND_MODEL_ID"], pretrained=True, modalities=["S1GRD"])
from terratorch import FULL_MODEL_REGISTRY
FULL_MODEL_REGISTRY.build(
    f'{os.environ["TERRAMIND_MODEL_ID"]}_generate',
    pretrained=True,
    modalities=["S1GRD"],
    output_modalities=["S2L2A"],
    timesteps=10,
    standardize=True,
)
PY

WORKDIR /app
COPY . /app
ENV PORT=8001 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/hf \
    HUGGINGFACE_HUB_CACHE=/models/hf/hub \
    TORCH_HOME=/models/torch \
    XDG_CACHE_HOME=/models/cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8001","--workers","1"]
```

> **Why HF_TOKEN is required at build time** — `facebook/sam3`, `facebook/sam3.1`, `facebook/dinov3-vitl16-pretrain-sat493m`, and `facebook/dinov3-vitl16-pretrain-lvd1689m` are gated under Meta's [SAM License](https://github.com/facebookresearch/sam3/blob/main/LICENSE) / DINOv3 license. The SAM 3 README explicitly states "request access to the checkpoints on the SAM 3 Hugging Face repo" before download. Prithvi (Apache-2.0) and TerraMind (Apache-2.0) are ungated, but we still pull them at build time so runtime is offline.

### 7.1 Offline deployment workflow

The service must run in an offline environment after one connected preparation step. Do **not** rely on third-party SAM3 mirror repositories for production; use official gated Meta/Hugging Face access, snapshot everything once, and carry the cache/image into the offline network.

1. On a connected build host, authenticate once with an approved `HF_TOKEN`.
2. Clone `facebookresearch/sam3` at a pinned commit and record that commit in `MODEL_MANIFEST.json`.
3. `snapshot_download()` all required HF repos into `/models/hf`:
   `facebook/sam3`, `facebook/sam3.1`, both DINOv3 ViT-L repos, Prithvi flood/burn/crop repos, and TerraMind.
4. Stage source imagery/video dependencies locally: GeoTIFF/COG scenes, SAR products, FMV clips, prompt JSON, and sample probes. No runtime path may assume internet access to remote COGs, HF, GitHub, or hosted videos.
5. Build `sentinelos-inference-sam3:gpu` with the local cache mounted/copied into the image or `sam3_models` volume.
6. Write SHA256 hashes for every checkpoint/config/tokenizer file into `MODEL_MANIFEST.json`; smoke-test `/health`, `/detect`, and `/detect_video` with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.
7. Export the Docker image plus `sam3_models` volume/cache bundle to the offline environment. Runtime containers keep network egress disabled and must fail fast if a model file is missing.

---

## 8. Backend Integration

### 8.1 `backend/worker.py`

```python
# Top of file (lines 32-44 area)
INFERENCE_SAM3_URL = os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001")
INFERENCE_PROVIDERS = {
    "yolo":      INFERENCE_URL,
    "lae-dino":  INFERENCE_LAE_DINO_URL,
    "mmrotate":  INFERENCE_MMROTATE_URL,
    "lsknet":    INFERENCE_LSKNET_URL,
    "sam2":      INFERENCE_SAM2_URL,
    "sam3":      INFERENCE_SAM3_URL,         # NEW
}

# Around line 495
CONSENSUS_EXEMPT_PROVIDERS = {"sam2", "sam3"}     # SAM3 is also class-agnostic + open-vocab

# Around line 729
GROUNDED_PROVIDERS: set[str] = {"sam2", "sam3"}   # both can refine boxes from phase-1
```

**Multispectral / SAR chip-emission branch** — the existing chipper at [worker.py:1123-1134](../backend/worker.py#L1123-L1134) currently emits uint8 RGB only. For SAM3 we add `_emit_chip_payload(window, src, providers)`:

```python
def _emit_chip_payload(window, src, providers, *, valid_mask):
    """Return (bytes, meta_kwargs) for the per-chip POST.

    Modality is selected based on the source raster and the providers requested:
      - SAR  : src.count==2 with VV/VH descriptions and 'sam3' in providers
      - MS   : src.count>=6 (HLS-style) and 'sam3' in providers
      - RGB  : everything else
    """
    sam3_selected = "sam3" in providers
    raw = src.read(window=window)
    descriptions = tuple((d or "").lower() for d in src.descriptions)
    window_transform = src.window_transform(window)
    geo_meta = {
        "source_crs": src.crs.to_string() if src.crs else None,
        "chip_transform": list(window_transform.to_gdal()),
        "chip_transform_order": "gdal",
        "source_window": [window.col_off, window.row_off, window.width, window.height],
        "source_bounds": list(src.window_bounds(window)),
    }

    has_vv_vh = {"vv", "vh"}.issubset({d.strip().lower() for d in descriptions})
    if sam3_selected and src.count == 2 and has_vv_vh:
        # 2-band float32 GeoTIFF in-memory write
        payload = _geotiff_window_bytes(src, window, indexes=[1, 2], dtype="float32")
        return payload, {"modality": "sar", "content_type": "image/tiff",
                          "filename": "chip.tif", "geo": geo_meta}

    if sam3_selected and src.count >= 6:
        payload = _geotiff_window_bytes(src, window, indexes=[1,2,3,4,5,6], dtype="float32")
        return payload, {"modality": "multispectral", "content_type": "image/tiff",
                          "filename": "chip.tif", "geo": geo_meta}

    # default RGB PNG
    rgb = chip_to_uint8_rgb(raw)
    return _png_bytes(rgb), {"modality": "rgb", "content_type": "image/png",
                              "filename": "chip.png", "geo": geo_meta}
```

The two helpers `_geotiff_window_bytes` and `_png_bytes` write to `io.BytesIO` and return `bytes`. The existing dispatch loop (currently `files={"image": ("chip.png", png_file, "image/png")}`) becomes:

```python
payload, meta_kwargs = _emit_chip_payload(window, src, selected_providers, valid_mask=valid_mask)
files  = {"image": (meta_kwargs["filename"], payload, meta_kwargs["content_type"])}
chip_meta_payload = json.dumps({**existing_chip_meta, **meta_kwargs})
```

**FMV pipeline** — new task `worker.process_fmv` (the symbol is already referenced at [backend/main.py:1124](../backend/main.py#L1124) as `workers.video.process_fmv` but the task does not yet exist). Implementation:

```python
@celery_app.task(name="worker.process_fmv", queue="imagery")
def process_fmv(clip_id: int, video_path: str, text_prompts: list[str], *,
                frame_stride: int = 1, max_frames: int | None = None) -> int:
    sess = requests.Session()
    payload = json.dumps({
        "video_path": video_path,
        "text_prompts": text_prompts or None,
        "frame_stride": frame_stride,
        "max_frames": max_frames,
    })
    resp = sess.post(
        f"{INFERENCE_PROVIDERS['sam3']}/detect_video",
        data={"metadata": payload}, stream=True,
        timeout=INFERENCE_CHIP_TIMEOUT_S * 60,    # videos are long
    )
    resp.raise_for_status()
    inserted = 0
    with postgis_db.get_cursor(commit=True) as cur:
        for line in resp.iter_lines(decode_unicode=True):
            if not line: continue
            entry = json.loads(line)
            cur.execute(
                """INSERT INTO fmv_detections (clip_id, frame_index, class,
                                                confidence, bbox, metadata)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb)""",
                (
                    clip_id,
                    entry["frame_index"],
                    entry["original_class"],
                    entry["score"],
                    json.dumps(_xyxy_to_normalized_cxcywh(entry["bbox_xyxy"])),
                    json.dumps({
                        "track_id": entry.get("track_id"),
                        "mask_rle": entry.get("mask_rle"),
                        "obb": entry.get("obb"),
                        "obb_format": entry.get("obb_format"),
                        "obb_source": entry.get("obb_source"),
                        "obb_angle_deg": entry.get("obb_angle_deg"),
                        "edge_truncated": entry.get("edge_truncated"),
                        "embedding": entry.get("embedding"),
                        "provider": "sam3",
                    }),
                ),
            )
            inserted += 1
    provider_lifecycle.mark_active(["sam3"])
    return inserted
```

The trigger in [backend/main.py:3424-3458](../backend/main.py#L3424-L3458) (FMV upload handler) appends a `process_fmv.delay(...)` call after the HLS prep when `auto_process=True` and a `text_prompts` form field is present.

### 8.2 `backend/detection_policy.py`

The runtime policy is already open-vocabulary: every label SAM3 emits passes through `parent_class_for_label`, which clusters into broad buckets and falls back to the normalized label itself if no cluster matches. The new `track`, `water`/`flood`, `burn_scar`, and `crop:*` labels are already routed in the canonical `detection_policy.py` (see [backend/detection_policy.py](../backend/detection_policy.py) — `_CLUSTER_RULES` plus the explicit `crop:` / `water` / `flood` / `burn_scar` branches in `parent_class_for_label`). No SAM3-specific thresholds are required, since the policy applies a single global floor (`GLOBAL_CONFIDENCE_FLOOR`, default `0.0`).

If an operator wants per-class floors for SAM3 outputs they set them via the JSON env override:

```bash
PER_CLASS_CONFIDENCE_OVERRIDES='{"track":0.35,"water":0.50,"burn_scar":0.50}'
```

### 8.3 `backend/main.py`

```python
_KNOWN_INFERENCE_PROVIDERS = (
    "yolo", "lae-dino", "mmrotate", "lsknet", "sam2", "sam3",   # added "sam3"
)
```

The FMV upload form gains an optional `text_prompts: str = Form(None)` and `inference_providers: str = Form("sam3")`. The handler around [backend/main.py:3424](../backend/main.py#L3424) fans out to `process_fmv` when `auto_process=True`.

### 8.4 `backend/provider_lifecycle.py`

```python
PROVIDER_TO_SERVICE = {
    "yolo": "inference",
    "lae-dino": "inference-lae-dino",
    "mmrotate": "inference-mmrotate",
    "lsknet": "inference-lsknet",
    "sam2": "inference-sam2",
    "sam3": "inference-sam3",       # NEW
}
PROVIDER_HEALTH_URLS["sam3"] = os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001")
```

### 8.5 `docker-compose.yml`

```yaml
inference-sam3:
  profiles: ["sam3", "all"]
  build:
    context: ./inference-sam3
    dockerfile: Dockerfile.gpu
    args:
      CUDA_VERSION: ${SAM3_CUDA_VERSION:?Run python scripts/configure_host.py first}
      TORCH_INDEX_URL: ${SAM3_TORCH_INDEX_URL:?...}
      TORCH_VERSION: ${SAM3_TORCH_VERSION:?...}
      TORCHVISION_VERSION: ${SAM3_TORCHVISION_VERSION:?...}
      TORCH_CUDA_ARCH_LIST: ${SAM3_TORCH_CUDA_ARCH_LIST:?...}
      HF_TOKEN: ${HF_TOKEN:?HF_TOKEN required for gated facebook/sam3 + facebook/sam3.1 + dinov3 weights}
      SAM3_IMAGE_MODEL_ID: ${SAM3_IMAGE_MODEL_ID:-facebook/sam3.1}
      SAM3_USE_MULTIPLEX: ${SAM3_USE_MULTIPLEX:-1}
      DINOV3_SAT_MODEL_ID: ${DINOV3_SAT_MODEL_ID:-facebook/dinov3-vitl16-pretrain-sat493m}
      DINOV3_LVD_MODEL_ID: ${DINOV3_LVD_MODEL_ID:-facebook/dinov3-vitl16-pretrain-lvd1689m}
      PRITHVI_BACKBONE_ID: ${PRITHVI_BACKBONE_ID:-ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL}
      TERRAMIND_MODEL_ID:  ${TERRAMIND_MODEL_ID:-terramind_v1_large}
  image: sentinelos-inference-sam3:gpu
  gpus: all
  environment:
    GPU_MODEL: ${GPU_MODEL:?Run python scripts/configure_host.py first}
    SAM3_GPU_PROFILE: ${SAM3_GPU_PROFILE:?Run python scripts/configure_host.py first}
    NVIDIA_VISIBLE_DEVICES: ${NVIDIA_VISIBLE_DEVICES:-all}
    NVIDIA_DRIVER_CAPABILITIES: ${NVIDIA_DRIVER_CAPABILITIES:-compute,utility}
    DEVICE: ${SAM3_DEVICE:-auto}
    CUDA_UNSUPPORTED_ARCH_POLICY: ${CUDA_UNSUPPORTED_ARCH_POLICY:-cpu}
    CPU_THREADS: auto
    WEB_CONCURRENCY: "1"
    SAM3_IMAGE_MODEL_ID: ${SAM3_IMAGE_MODEL_ID:-facebook/sam3.1}
    SAM3_USE_MULTIPLEX:  ${SAM3_USE_MULTIPLEX:-1}
    DINOV3_SAT_MODEL_ID: ${DINOV3_SAT_MODEL_ID:-facebook/dinov3-vitl16-pretrain-sat493m}
    DINOV3_LVD_MODEL_ID: ${DINOV3_LVD_MODEL_ID:-facebook/dinov3-vitl16-pretrain-lvd1689m}
    PRITHVI_BACKBONE_ID: ${PRITHVI_BACKBONE_ID:-ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL}
    TERRAMIND_MODEL_ID:  ${TERRAMIND_MODEL_ID:-terramind_v1_large}
    SAM3_TEXT_THRESHOLD: "0.30"
    SAM3_BOX_THRESHOLD:  "0.25"
    SAM3_IMAGE_SIZE: "1008"
    SAM3_PRITHVI_OVERLAY_THRESHOLD: "0.30"
    SAM3_SAR_CONF_CAP: "0.85"
    SAM3_OBB_OPENING_KERNEL_PCT: "0.01"
    SAM3_OBB_MIN_AREA_PX: "4"
    SAM3_DEFAULT_PROMPT_PROFILE: ""               # empty = auto-select per modality
    SAM3_MAX_PROMPTS_PER_REQUEST: "1024"
    DETECTION_THRESHOLD_PROFILE: open
    MODEL_VERSION: sam3-image+sam3.1-video+dinov3-sat-l+prithvi-600m-tl+terramind-large-v1
    HF_HOME: /models/hf
    HUGGINGFACE_HUB_CACHE: /models/hf/hub
    TORCH_HOME: /models/torch
    XDG_CACHE_HOME: /models/cache
    HF_HUB_OFFLINE: "1"
    TRANSFORMERS_OFFLINE: "1"
  volumes:
    - ./inference-sam3:/app
    - sam3_models:/models
    - imagery_data:/data/imagery:ro
    - fmv_data:/data/fmv:ro             # NEW — FMV needs read access on the worker volume
  working_dir: /app
  healthcheck:
    test: ["CMD-SHELL", "python3 -c 'import socket; s=socket.socket(); s.settimeout(2); exit(0 if s.connect_ex((\"127.0.0.1\",8001))==0 else 1)' || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 60s        # weight-resident services take longer than SAM2 to load

# in `volumes:` block:
sam3_models:
fmv_data:                    # NEW — already implied by FMV_PATH=/data/fmv
```

Add to `backend.environment` and `worker.environment`:

```
- INFERENCE_SAM3_URL=http://inference-sam3:8001
```

### 8.6 `.env.example` additions

```env
# ── SAM3 inference service ─────────────────────────────────────────────────────
HF_TOKEN=                                # required: gated SAM3/SAM3.1 + DINOv3 weights
INFERENCE_SAM3_URL=http://inference-sam3:8001

SAM3_DEVICE=auto
SAM3_IMAGE_MODEL_ID=facebook/sam3.1     # also valid: facebook/sam3
SAM3_USE_MULTIPLEX=1                     # 1 = build_sam3_multiplex_video_predictor (SAM 3.1)
                                         # 0 = build_sam3_video_predictor          (plain SAM 3)
DINOV3_SAT_MODEL_ID=facebook/dinov3-vitl16-pretrain-sat493m
DINOV3_LVD_MODEL_ID=facebook/dinov3-vitl16-pretrain-lvd1689m
PRITHVI_BACKBONE_ID=ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL
TERRAMIND_MODEL_ID=terramind_v1_large

SAM3_TEXT_THRESHOLD=0.30
SAM3_BOX_THRESHOLD=0.25
SAM3_IMAGE_SIZE=1008
SAM3_PRITHVI_OVERLAY_THRESHOLD=0.30
SAM3_SAR_CONF_CAP=0.85
SAM3_OBB_OPENING_KERNEL_PCT=0.01
SAM3_OBB_MIN_AREA_PX=4
SAM3_DEFAULT_PROMPT_PROFILE=               # empty → auto: satellite_v1 for rgb/multispectral/sar, ground_v1 for fmv
SAM3_MAX_PROMPTS_PER_REQUEST=1024
SAM3_LABEL_FILE=                            # optional override JSON file (overrides auto-select)

# Build-time GPU args (filled by scripts/configure_host.py)
SAM3_CUDA_VERSION=12.6.3
SAM3_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126
SAM3_TORCH_VERSION=2.7.1
SAM3_TORCHVISION_VERSION=0.22.1
SAM3_TORCH_CUDA_ARCH_LIST=8.0;8.6;8.9;9.0;12.0+PTX
SAM3_GPU_PROFILE=h100-80gb
```

---

## 9. Verification

### 9.1 Smoke

```bash
docker compose --profile sam3 build inference-sam3
docker compose --profile sam3 up -d inference-sam3
curl -fsS http://localhost:8008/health | jq .   # external port mapping in compose
```

Expected health body (subset):

```json
{
  "status": "ok",
  "model_loaded": true,
  "model_versions": {
    "sam3_image":    "facebook/sam3.1",
    "sam3_video":    "sam3.1-multiplex",
    "dinov3_sat":    "facebook/dinov3-vitl16-pretrain-sat493m",
    "dinov3_lvd":    "facebook/dinov3-vitl16-pretrain-lvd1689m",
    "prithvi_backbone": "ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL",
    "prithvi_flood":    "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11",
    "prithvi_burn":     "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars",
    "prithvi_crop":     "ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification",
    "terramind":        "terramind_v1_large"
  }
}
```

### 9.2 Per-modality

**A. RGB text-prompt:**
```bash
curl -F image=@sample/chicago_chip.png \
     -F 'metadata={"text_prompts":["a ship","an airplane"],"modality":"rgb"}' \
     http://localhost:8008/detect | jq '.detections[] | {original_class, confidence}'
```

**B. RGB box-prompt (SAM2-compatible):**
```bash
curl -F image=@sample/chicago_chip.png \
     -F 'metadata={"prompt_boxes":[{"bbox":[0.5,0.5,0.1,0.1],"class":"ship"}]}' \
     http://localhost:8008/detect | jq '.detections[].mask_rle | length'
```

**C. Multispectral:**
```bash
curl -F image=@sample/hls6_chip.tif \
     -F 'metadata={"modality":"multispectral"}' \
     http://localhost:8008/detect | jq '.detections[].prithvi_labels'
```

**D. SAR:**
```bash
curl -F image=@sample/s1_grd_chip.tif \
     -F 'metadata={"modality":"sar","text_prompts":["a ship","an oil tanker"]}' \
     http://localhost:8008/detect | jq '.detections[] | {original_class, sar_proxy, confidence}'
```

**E. FMV:**
```bash
curl -F video=@sample/clip.mp4 \
     -F 'metadata={"text_prompts":["a person","a car"],"frame_stride":2}' \
     http://localhost:8008/detect_video > /tmp/det.ndjson
wc -l /tmp/det.ndjson                      # one entry per frame×track
jq -r 'select(.frame_index==0) | "\(.track_id) \(.original_class) \(.score)"' /tmp/det.ndjson
```

### 9.3 End-to-end through the worker

```bash
# Imagery (multispectral)
curl -F file=@sample/hls_scene.tif \
     -F sensor_type=Multispectral \
     -F inference_providers=sam3 \
     http://localhost/api/ingest/upload
# → poll /api/detections → expect bbox + obb + mask_rle + dinov3_embedding + prithvi_labels.

# FMV
curl -F file=@sample/clip.mp4 \
     -F sensor_type=FMV \
     -F text_prompts="a car,a person" \
     -F inference_providers=sam3 \
     http://localhost/api/ingest/upload
# → poll /api/fmv/clips/<id>/detections → tracks visible.
```

### 9.4 Tests (`pytest inference-sam3/tests`)

| Test | Asserts |
|---|---|
| `test_health.py` | `/health` returns 200; lists all expected model IDs (without loading them — fixture monkeypatches loaders to return stubs). |
| `test_text_prompt_rgb.py` | Stub `Sam3Processor` returns 1 mask for prompt "a ship"; response has 1 detection with `original_class=="a ship"`, normalized bbox in [0,1], `embedding.dim==1024`. |
| `test_box_prompt.py` | `metadata.prompt_boxes` triggers `run_box_prompts`; SAM2-style metadata accepted; `class` inherited. |
| `test_multispectral.py` | 6-band float32 GeoTIFF chip → response has `modality="multispectral"`, `prithvi_labels` populated when stub overlay matches. Verifies `constant_scale=0.0001` is applied. |
| `test_sar.py` | 2-band SAR chip → `modality="sar"`, `sar_proxy=True`, `confidence ≤ SAM3_SAR_CONF_CAP`, `terramind_embedding.dim==768`. |
| `test_video.py` | Stub video predictor yields 3 frames × 2 tracks; NDJSON streaming response has 6 entries with `obb`; first-frame entries carry `embedding`, subsequent frames don't. |
| `test_prompts_loader.py` | `metadata.text_prompts` overrides profile; profile resolution works; dedupe + max-prompts cap enforced; empty list raises 400. |
| `test_fusion.py` | RLE encode/decode round-trip; mask-aware NMS keeps highest-confidence per class; rotated mask produces normalized 8-corner OBB; empty/noisy masks fall back to HBB OBB. |
| `test_obb_exports.py` | DOTA, YOLO-OBB, fast pixel-corner GeoJSON, and GIS-grade mask-to-map OBB exporters preserve label/confidence; map exporter uses Rasterio polygonization and returns `None` for degenerate masks. |
| `test_offline_cache.py` | With `HF_HUB_OFFLINE=1`, missing model files fail fast; no HTTP calls are attempted by loaders. |

Backend tests:

| Test | Asserts |
|---|---|
| `backend/tests/test_inference_providers.py` | `_parse_inference_providers("yolo,sam3")` accepts `sam3`. |
| `backend/tests/test_chip_emitter.py` (NEW) | `_emit_chip_payload` returns `image/tiff` + `modality=multispectral` for ≥6-band raster, `image/tiff` + `modality=sar` for 2-band VV/VH, `image/png` + `modality=rgb` otherwise, and always includes CRS/window transform metadata for geospatial export when the source raster has it. |
| `backend/tests/test_grounded_dispatch.py` (NEW) | When `selected_providers=["yolo","sam3"]`, phase-1 dispatches to YOLO; phase-2 dispatches to SAM3 with `metadata.prompt_boxes` populated from YOLO response (mirrors existing SAM2 path at [worker.py:809-848](../backend/worker.py#L809-L848)). |

---

## 10. Hardware & Operational Constraints

- **VRAM (rough planning estimate, not a sourced benchmark):** SAM3 has 848M parameters per the README, so FP16 weights alone are ~1.7 GB before runtime overhead. DINOv3-SAT-L and DINOv3-LVD-L are 0.3B each (~600 MB FP16 each), Prithvi-600M-TL is ~1.2 GB FP16, Prithvi task heads and TerraMind add several more GB. Plan for **at least 24 GB GPU VRAM** for the default all-in-one service and benchmark before enabling all optional heads concurrently.
- **VRAM with DINOv3 ViT-7B opt-in:** add ~14 GB FP16 per 7B DINOv3 model, plus activation overhead. Recommend H100-80GB or A100-80GB for 7B variants.
- **CPU memory:** 32 GB recommended (TerraMind generation and raster windows buffer tensors).
- **Disk:** size the model cache empirically during build; start with 25–40 GB for SAM3, two DINOv3 ViT-L checkpoints, Prithvi heads, and TerraMind.
- **Latency targets:** do not treat this plan as a latency source of truth. Record smoke-benchmark numbers per GPU profile after implementation; SAM 3.1 Object Multiplex only improves video multi-object tracking.
- **Prefer `start_period: 60s`** in healthcheck — weight-resident services take longer to come up than SAM2.
- **Offline runtime:** all weights baked at build time; `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` prevent runtime fetches. The dev volume mount (`./inference-sam3:/app`) does not affect cached weights.
- **License notes:**
  - SAM3 code and weights: Meta **SAM License**; the license includes trade-control and prohibited-use terms, so legal review is mandatory for operational deployment.
  - DINOv3 weights: Meta DINOv3 custom license.
  - Prithvi-EO-2.0: Apache 2.0.
  - TerraMind: Apache 2.0.

---

## 11. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| SAM3 / DINOv3 HF gating blocks build | High | Document `HF_TOKEN` requirement in `.env.example`; build script aborts with a clear message; provide pre-built image distribution path for air-gapped customers. |
| TerraMind S1GRD→S2L2A proxy hallucination on SAR | Medium | Cap SAM3-on-SAR confidence at `SAM3_SAR_CONF_CAP=0.85`; tag all SAR detections `review_candidate`; analyst-review-required policy. |
| Prompt-loop latency at 1 024 prompts | Medium | Operators tune via `metadata.text_prompts`; use SAM3 batched image inference where possible. SAM 3.1's Object Multiplex helps **video** mode only. |
| FMV throughput < real-time | Medium | `frame_stride` env knob; OBJ-MX runs ~7× faster than per-object SAM3; analyst can re-run dense passes on shorter clips. |
| OBB geometry too noisy on tiny/aliased masks | Medium | Keep mask RLE and HBB, expose `SAM3_OBB_OPENING_KERNEL_PCT` and `SAM3_OBB_MIN_AREA_PX`, flag `obb_source=hbb_fallback` and `edge_truncated` for review. |
| Map-space OBB angles/dimensions wrong because CRS/affine metadata is missing or geographic | Medium | Require `metadata.geo.chip_transform` and `source_crs` for GIS export; compute metric OBBs only after polygonization and reprojection to a local projected CRS; otherwise emit pixel-space OBB with an export warning. |
| Offline cache incomplete | High | Build-time manifest with SHA256s; smoke-test with `HF_HUB_OFFLINE=1`; runtime network disabled and loaders fail fast on missing files. |
| Third-party SAM3 mirrors conflict with license/provenance controls | Medium | Production uses official gated Meta/HF repos only; mirrors are not part of the deployment path. |
| Prithvi flood/burn inference size mismatch | Medium | Mirror the released HF inference scripts' 512-window flow for those heads; use the crop head only with documented 18-band temporal input. |
| 2-band SAR chip is not VV/VH (e.g. HH/HV) | Medium | TerraMind docs list S1GRD/S1RTC bands as VV and VH; reject HH/HV for the TerraMind path unless a separate validated adapter is added. |
| Worker chunk emission cost (TIFF in memory) | Low | Use `rasterio.MemoryFile` + `dst.write` once per chip; no on-disk spill. |
| Two SAM-family services (sam2 + sam3) in same compose | Low | They're on separate profiles; lifecycle manager idle-stops the unused one within `IDLE_COOLDOWN_S=600`. Frontend should default to SAM3 once smoke passes. |
| Prompt wording hurts model recall | Low | Default `SAM3_PROMPT_TEMPLATE="{label}"` keeps prompts as short noun phrases; operators can set a modality-specific template only after local benchmarks show it helps. |

---

## 12. Critical Files

**New (under `inference-sam3/`):**
- `Dockerfile.gpu`, `requirements.txt`, `main.py`,
- `multispectral.py`, `sar.py`, `terramind.py`,
- `prithvi_heads.py`, `embedding.py`, `sam3_runner.py`, `fusion.py`,
- `exports/obb.py`, `MODEL_MANIFEST.json`,
- `prompts/{loader.py, satellite_v1.json, ground_v1.json}`,
- `probes/probe_chip.png`,
- `tests/{test_health,test_text_prompt_rgb,test_box_prompt,test_multispectral,test_sar,test_video,test_prompts_loader,test_fusion,test_obb_exports,test_offline_cache}.py`.

**Modified:**
- [docker-compose.yml](../docker-compose.yml) — new service `inference-sam3`, volumes `sam3_models`+`fmv_data`, env on `backend` and `worker`.
- [backend/worker.py](../backend/worker.py) (lines 32-44, 495, 729, ~1123) — register provider, extend `GROUNDED_PROVIDERS` and `CONSENSUS_EXEMPT_PROVIDERS`, add `_emit_chip_payload`, add `process_fmv` task.
- [backend/main.py](../backend/main.py) (line 3294) — add `sam3` to `_KNOWN_INFERENCE_PROVIDERS`; FMV upload form gains `text_prompts`.
- [backend/detection_policy.py](../backend/detection_policy.py) — already has open-vocabulary routing plus explicit `track`, `crop:`, `flood`/`water`, and `burn_scar` branches; no SAM3-specific threshold code is required unless an operator wants env overrides.
- [backend/provider_lifecycle.py](../backend/provider_lifecycle.py) (lines 24-38) — add `sam3` mapping.
- [.env.example](../.env.example) — add SAM3_*, DINOV3_*, PRITHVI_*, TERRAMIND_*, HF_TOKEN.
- [README.md](../README.md) — document new endpoint, modality matrix, examples.

**Reusable utilities (read but do not modify):**
- [inference-sam2/main.py:56-216](../inference-sam2/main.py#L56-L216) — multi-GPU pool + `_auto_cuda_devices` (copied verbatim into `sam3_runner.resolve_devices`).
- [inference-sam2/main.py:280-342](../inference-sam2/main.py#L280-L342) — `_normalize_prompt_boxes`, `_mask_to_obb_normalized` (adapted into `fusion.py`).
- [backend/worker.py:350-359](../backend/worker.py#L350-L359) — `chip_to_uint8_rgb` (referenced by `multispectral.hls_to_rgb_preview`).
- [backend/worker.py:362-419](../backend/worker.py#L362-L419) — `valid_data_mask`, `clip_box_to_valid_mask` (passed through to SAM3 to suppress detections in nodata regions).
- [backend/detection_policy.py:146-191](../backend/detection_policy.py#L146-L191) — `parent_class_for_label` (called in `fusion.candidate_to_detection`).

---

## 13. Sources Cited

- SAM 3 GitHub README — [github.com/facebookresearch/sam3](https://github.com/facebookresearch/sam3)
- SAM 3 license — [github.com/facebookresearch/sam3/LICENSE](https://github.com/facebookresearch/sam3/blob/main/LICENSE)
- SAM 3 paper — [arXiv 2511.16719](https://arxiv.org/html/2511.16719v2)
- Transformers SAM3 model docs — [huggingface.co/docs/transformers/en/model_doc/sam3](https://huggingface.co/docs/transformers/en/model_doc/sam3)
- SAM 3 image predictor notebook — [`sam3_image_predictor_example.ipynb`](https://github.com/facebookresearch/sam3/blob/main/examples/sam3_image_predictor_example.ipynb)
- SAM 3.1 release notes — [RELEASE_SAM3p1.md](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md)
- SAM 3.1 video predictor notebook — [`sam3.1_video_predictor_example.ipynb`](https://github.com/facebookresearch/sam3/blob/main/examples/sam3.1_video_predictor_example.ipynb)
- SAM 3 Hugging Face cards — [facebook/sam3](https://huggingface.co/facebook/sam3), [facebook/sam3.1](https://huggingface.co/facebook/sam3.1)
- NVIDIA CUDA Docker tag — [`nvidia/cuda:12.6.3-cudnn-devel-ubuntu24.04`](https://hub.docker.com/layers/nvidia/cuda/12.6.3-cudnn-devel-ubuntu24.04/images/sha256-c51bfc8bcd4febe3e26952615496b4347767f61f9079f08ffc914b42905e510d)
- DINOv3 GitHub — [github.com/facebookresearch/dinov3](https://github.com/facebookresearch/dinov3)
- DINOv3 SAT-493M card — [facebook/dinov3-vitl16-pretrain-sat493m](https://huggingface.co/facebook/dinov3-vitl16-pretrain-sat493m)
- DINOv3 LVD-1689M card — [facebook/dinov3-vitl16-pretrain-lvd1689m](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m)
- Prithvi-EO-2.0 paper — [arXiv 2412.02732](https://arxiv.org/abs/2412.02732)
- Prithvi-EO-2.0 GitHub — [github.com/NASA-IMPACT/Prithvi-EO-2.0](https://github.com/NASA-IMPACT/Prithvi-EO-2.0)
- Prithvi org — [huggingface.co/ibm-nasa-geospatial](https://huggingface.co/ibm-nasa-geospatial)
- Prithvi Sen1Floods11 head — [ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11)
- Prithvi Sen1Floods11 config — [configs/sen1floods11.yaml](https://github.com/NASA-IMPACT/Prithvi-EO-2.0/blob/main/configs/sen1floods11.yaml)
- Prithvi Sen1Floods11 inference script — [inference.py](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11/blob/main/inference.py)
- Prithvi BurnScars card/inference script — [model card](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars), [inference.py](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars/blob/main/inference.py)
- Prithvi crop model card — [Prithvi-EO-1.0-100M-multi-temporal-crop-classification](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification)
- TerraMind-1.0-large card — [ibm-esa-geospatial/TerraMind-1.0-large](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-large)
- TerraTorch TerraMind guide — [terrastackai.github.io/terratorch/1.1/guide/terramind](https://terrastackai.github.io/terratorch/1.1/guide/terramind/)
- TerraMind paper — [arXiv 2504.11171](https://arxiv.org/html/2504.11171v1)
- IBM Research TerraMind blog — [research.ibm.com/blog/terramind-esa-earth-observation-model](https://research.ibm.com/blog/terramind-esa-earth-observation-model)
- TerraFM (compared, rejected) — [arXiv 2506.06281](https://arxiv.org/html/2506.06281v1)
- Clay v1.5 (compared, rejected) — [made-with-clay/Clay](https://huggingface.co/made-with-clay/Clay)
- xView Challenge taxonomy — [xviewdataset.org](https://xviewdataset.org/)
- DOTA benchmark — [captain-whu.github.io/DOTA](https://captain-whu.github.io/DOTA/)
- DIOR benchmark — [gcheng-nwpu.github.io](http://www.escience.cn/people/gongcheng/DIOR.html)
- Functional Map of the World — [github.com/fMoW/dataset](https://github.com/fMoW/dataset)
- FAIR1M — [arXiv 2103.05569](https://arxiv.org/abs/2103.05569)
- HRSC2016 mirror — [Kaggle HRSC2016](https://www.kaggle.com/datasets/guofeng/hrsc2016)
- RarePlanes — [WACV 2021 paper](https://openaccess.thecvf.com/content/WACV2021/papers/Shermeyer_RarePlanes_Synthetic_Data_Takes_Flight_WACV_2021_paper.pdf)
- LVIS dataset — [openaccess.thecvf.com](https://openaccess.thecvf.com/content_CVPR_2019/papers/Gupta_LVIS_A_Dataset_for_Large_Vocabulary_Instance_Segmentation_CVPR_2019_paper.pdf)
- COCO dataset — [cocodataset.org](https://cocodataset.org/)
- Objects365 — [objects365.org](https://www.objects365.org/)
- HBB2OBB mask-to-OBB workflow — [github.com/rfonod/hbb2obb](https://github.com/rfonod/hbb2obb)
- OpenCV `minAreaRect` / `boxPoints` docs — [docs.opencv.org](https://docs.opencv.org/4.x/d3/dc0/group__imgproc__shape.html)
- Ultralytics YOLO OBB dataset format — [docs.ultralytics.com/datasets/obb](https://docs.ultralytics.com/datasets/obb/)
- SamGeo SAM3 tiled GeoTIFF segmentation — [samgeo.gishub.org](https://samgeo.gishub.org/examples/sam3_tiled_segmentation/)
- geosam `sam_detect` docs — [walker-data.com/geosam/reference/sam_detect.html](https://walker-data.com/geosam/reference/sam_detect.html)
- SegEarth-OV3 paper — [arXiv 2512.08730](https://arxiv.org/abs/2512.08730)
- Rasterio vector features / polygonization — [rasterio.readthedocs.io](https://rasterio.readthedocs.io/en/stable/topics/features.html)
- GDAL geotransform tutorial — [gdal.org](https://gdal.org/en/stable/tutorials/geotransforms_tut.html)
- Shapely `minimum_rotated_rectangle` — [shapely.readthedocs.io](https://shapely.readthedocs.io/en/2.0.0/reference/shapely.minimum_rotated_rectangle.html)
- PostGIS `ST_OrientedEnvelope` — [postgis.net](https://postgis.net/docs/manual-3.1/ST_OrientedEnvelope.html)
- X-AnyLabeling SAM3/OBB support — [github.com/CVHub520/X-AnyLabeling](https://github.com/CVHub520/X-AnyLabeling)
- CVAT SAM3 image segmentation changelog — [cvat.ai](https://www.cvat.ai/resources/changelog/sam-3-image-segmentation)
- SAR-FM survey (Ice-FMBench) — [arXiv 2503.22516](https://arxiv.org/html/2503.22516v1)
- SAM-on-SAR adaptation (avalanche) — [MDPI Remote Sensing 18/3/519](https://www.mdpi.com/2072-4292/18/3/519)
