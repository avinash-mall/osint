# `inference-sam3/yoloe.py` — YOLOE-26x-seg(-pf) Tracker

**Path:** [inference-sam3/yoloe.py](../../inference-sam3/yoloe.py)
**Lines:** ~289
**Depends on:** `ultralytics`, weights `inference-sam3/yoloe-26x-seg.pt` + `yoloe-26x-seg-pf.pt`, MobileCLIP2 text encoder `mobileclip2_b.ts`

## Purpose

Standalone FMV tracker. Replaces the removed SAM3 AMG path — see [decisions/why-yoloe-replaced-amg.md](../decisions/why-yoloe-replaced-amg.md). Emits `(mask, bbox, label, score)` in one forward pass; no second labeling model required.

## Key symbols

- [`_patch_mobileclip_asset_path`](../../inference-sam3/yoloe.py#L44) — Ultralytics looks for MobileCLIP2 at a path that doesn't exist on the container; patches the lookup.
- [`load`](../../inference-sam3/yoloe.py#L81) — builds the YOLOE bundle on a device (loads `-seg` if text-prompted, `-pf` for prompt-free, both if both will be used).
- [`run`](../../inference-sam3/yoloe.py#L142) — `(image, text_prompts) -> [(mask, bbox, score, label), ...]`.
- [`_resize_mask`](../../inference-sam3/yoloe.py#L237), [`_bbox_from_mask_fallback`](../../inference-sam3/yoloe.py#L252), [`_bbox_mask`](../../inference-sam3/yoloe.py#L268).
- [`model_versions`](../../inference-sam3/yoloe.py#L277).

## Why two heads

- **`-seg` (text-promptable)** — `metadata.text_prompts` non-empty → this head accepts the prompt list, emits class-labeled detections.
- **`-pf` (prompt-free)** — `text_prompts` empty → this head emits boxes from its baked-in LVIS-style vocabulary (4 585 classes — many scene/concept entries like `winter morning`, `wine cooler`, `anniversary`). Useful for "discover anything" exploration but expect scene-level labels alongside object detections.

## Reachable from both FMV and imagery

- **FMV path** — `POST /api/fmv/clips` with `model=yolo26 + prompt_mode=amg|pcs` enqueues `process_fmv` with `worker_mode="yoloe"`. The worker hits `/detect_video`, which calls [`sam3_runner.run_video_yoloe`](../../inference-sam3/sam3_runner.py#L1317) once per chunked window. Per-frame `yoloe.run(bundle, frame, prompts, threshold)` produces tracked detections.
- **Imagery path** — `POST /api/ingest/upload` with `model=yolo26 + prompt_mode=amg|pcs` rewrites the request's `enabled_layers` to `["yoloe_pf"]` or `["yoloe_seg"]`. The worker tiles the raster into chips and posts each to `/detect`, where [`_detect_pipeline`](../../inference-sam3/main.py#L893) detects the YOLOE-exclusive mode (`_enabled in ({"yoloe_pf"}, {"yoloe_seg"})`) and calls `yoloe.run` per chip *instead of* SAM3. SAM3 / DOTA-OBB / Grounding-DINO / Prithvi are all skipped in this mode. See [decisions/why-imagery-yoloe-mirrors-fmv.md](../decisions/why-imagery-yoloe-mirrors-fmv.md).

## Precision and dtype

YOLOE is pinned to fp32 (`YOLOE_HALF = False`, `YOLOE_CHANNELS_LAST = False` in [yoloe.py:36-37](../../inference-sam3/yoloe.py#L36-L37)) regardless of the `SAM3_YOLO_HALF` env var that `scripts/gpu_profiles.py` may emit. Reason: ultralytics' YOLOE `Lrpc` vocab head keeps fp32 sub-modules even after `model.half()`, so a half-cast body trips `mat1 and mat2 must have the same dtype` inside `set_classes()` and the SwiGLU `w12` Linear during `predict()`, silently emitting zero detections.

The `boxes`/`masks` tensors returned by `predict()` can still be bf16 (autocast or model internals), and `Tensor.numpy()` refuses to convert `BFloat16`. The extraction block in [yoloe.py#run](../../inference-sam3/yoloe.py#L210-L226) explicitly casts to fp32 with `.float().cpu().numpy()` before handing off. Without that cast every YOLOE call (PF and SEG, GPU and CPU under autocast) silently swallows results via the bare `except Exception: continue` and emits zero detections — exactly the regression that masked clip-level FMV tracking output until 2026-05-26. See [decisions/why-yoloe-fp32-and-bf16-cast.md](../decisions/why-yoloe-fp32-and-bf16-cast.md).

## Cross-references

- [decisions/why-yoloe-replaced-amg.md](../decisions/why-yoloe-replaced-amg.md)
- [sam3-pcs-multiplex-video.md](sam3-pcs-multiplex-video.md) — the alternative tracker
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
