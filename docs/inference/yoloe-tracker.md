# `inference-sam3/yoloe.py` — YOLOE-26x-seg(-pf) Tracker

**Path:** [inference-sam3/yoloe.py](../../inference-sam3/yoloe.py)
**Lines:** ~320
**Depends on:** `ultralytics`, weights `inference-sam3/yoloe-26x-seg.pt` + `yoloe-26x-seg-pf.pt`, MobileCLIP2 text encoder `mobileclip2_b.ts`

## Purpose

Standalone FMV tracker. Replaces the removed SAM3 AMG path — see [decisions/why-yoloe-replaced-amg.md](../decisions/why-yoloe-replaced-amg.md). Emits `(mask, bbox, label, score)` in one forward pass; no second labeling model required.

## Key symbols

- [`_patch_mobileclip_asset_path`](../../inference-sam3/yoloe.py#L50) — Ultralytics looks for MobileCLIP2 at a path that doesn't exist on the container; patches the lookup.
- [`load`](../../inference-sam3/yoloe.py#L87) — builds the YOLOE bundle on a device (loads `-seg` if text-prompted, `-pf` for prompt-free, both if both will be used).
- [`run`](../../inference-sam3/yoloe.py#L148) — `(image, text_prompts) -> [(mask, bbox, score, label), ...]`. When the `-pf` checkpoint is unavailable and there are **no** prompts, the seg fallback runs with the model's baked vocabulary instead of calling `set_classes([], get_text_pe([]))` (which raised / left a zero-class vocab, so the fallback always emitted nothing). Both except blocks re-raise when `sam3_runner._cuda_context_poisoned(exc)` matches, instead of returning `[]`, so the caller's `os._exit(1)` self-heal fires — see [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md).
- [`YOLOE_IMGSZ`](../../inference-sam3/yoloe.py#L30) — inference image size passed to `predict()`. Default is now **896** (was 640) — ~1.96× pixels-per-object for distant / densely-packed FMV targets; /32-aligned, tunable to 960/1024 for very dense scenes or back to 640 for speed. See [decisions/dense-scene-recall-defaults.md](../decisions/dense-scene-recall-defaults.md).
- [`_resize_mask`](../../inference-sam3/yoloe.py#L268), [`_bbox_from_mask_fallback`](../../inference-sam3/yoloe.py#L283), [`_bbox_mask`](../../inference-sam3/yoloe.py#L299).
- [`model_versions`](../../inference-sam3/yoloe.py#L308).

## Why two heads

- **`-seg` (text-promptable)** — `metadata.text_prompts` non-empty → this head accepts the prompt list, emits class-labeled detections.
- **`-pf` (prompt-free)** — `text_prompts` empty → this head emits boxes from its baked-in LVIS-style vocabulary (4 585 classes — many scene/concept entries like `winter morning`, `wine cooler`, `anniversary`). Useful for "discover anything" exploration but expect scene-level labels alongside object detections.

## Reachable from FMV only

`POST /api/fmv/clips` with `model=yolo26 + prompt_mode=amg|pcs` enqueues `process_fmv` with `worker_mode="yoloe"`. The worker hits `/detect_video`, which calls [`sam3_runner.run_video_yoloe`](../../inference-sam3/sam3_runner.py#L1534) once per chunked window. Per-frame `yoloe.run(bundle, frame, prompts, threshold)` produces tracked detections.

Still-image `/detect` and `/detect_raw` reject `yoloe`, `yoloe_pf`, and `yoloe_seg` layers; satellite imagery uses the SAM3 sensor pipeline plus DOTA-OBB and other specialists. See [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md).

## Precision and dtype

YOLOE is pinned to fp32 (`YOLOE_HALF = False`, `YOLOE_CHANNELS_LAST = False` in [yoloe.py:38-39](../../inference-sam3/yoloe.py#L38-L39)) regardless of the `SAM3_YOLO_HALF` env var that `scripts/gpu_profiles.py` may emit. Reason: ultralytics' YOLOE `Lrpc` vocab head keeps fp32 sub-modules even after `model.half()`, so a half-cast body trips `mat1 and mat2 must have the same dtype` inside `set_classes()` and the SwiGLU `w12` Linear during `predict()`, silently emitting zero detections.

The `boxes`/`masks` tensors returned by `predict()` can still be bf16 (autocast or model internals), and `Tensor.numpy()` refuses to convert `BFloat16`. The extraction block in [yoloe.py#run](../../inference-sam3/yoloe.py#L236-L256) explicitly casts to fp32 with `.float().cpu().numpy()` before handing off. Without that cast every YOLOE call (PF and SEG, GPU and CPU under autocast) silently swallows results via the bare `except Exception: continue` and emits zero detections — exactly the regression that masked clip-level FMV tracking output until 2026-05-26. See [decisions/why-yoloe-fp32-and-bf16-cast.md](../decisions/why-yoloe-fp32-and-bf16-cast.md).

## Cross-references

- [decisions/why-yoloe-replaced-amg.md](../decisions/why-yoloe-replaced-amg.md)
- [decisions/dense-scene-recall-defaults.md](../decisions/dense-scene-recall-defaults.md) — `YOLOE_IMGSZ` 640→896 default
- [sam3-pcs-multiplex-video.md](sam3-pcs-multiplex-video.md) — the alternative tracker
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md) — pf→seg fallback fix + poisoned-context re-raise
