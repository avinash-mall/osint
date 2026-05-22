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
- **`-pf` (prompt-free)** — `text_prompts` empty → this head emits boxes from its baked-in vocabulary; useful for "find anything that looks like a known object class" in clips where the operator can't specify upfront.

## Cross-references

- [decisions/why-yoloe-replaced-amg.md](../decisions/why-yoloe-replaced-amg.md)
- [sam3-pcs-multiplex-video.md](sam3-pcs-multiplex-video.md) — the alternative tracker
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
