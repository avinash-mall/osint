# `inference-sam3/dota_obb.py` — DOTA-OBB Specialist

**Path:** [inference-sam3/dota_obb.py](../../inference-sam3/dota_obb.py)
**Lines:** ~149
**Depends on:** `ultralytics` (`yolo11n-obb.pt`)

## Purpose

Closed-vocab oriented-bounding-box detector for the 18 DOTA-v1 classes (plane, ship, vehicle, bridge, etc.). The single biggest quality win in the image stack — see [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md): DOTA-OBB alone raises mAP from 0.05 to 0.61 on DOTA val.

## Key symbols

- [`load`](../../inference-sam3/dota_obb.py#L34) — builds the DOTA bundle.
- [`run`](../../inference-sam3/dota_obb.py#L62) — runs YOLO11n-OBB on an image; returns `(mask, bbox, score, label)` tuples. Mask is a polygon-filled rectangle in the OBB shape (real segmentation isn't part of YOLO11-OBB).
- [`_polygon_mask`](../../inference-sam3/dota_obb.py#L121) — fills a polygon mask from OBB corner points.
- [`model_versions`](../../inference-sam3/dota_obb.py#L140).

## Gating

DOTA-OBB runs only when loaded **and** the request is relevant to DOTA classes. The gate is the operator's `metadata.enabled_layers`, `metadata.force_dota_obb`, and the runtime check `_prompts_relevant_to_dota` in [main.py#L436](../../inference-sam3/main.py#L436). If the prompt list has zero overlap with DOTA's 18 classes, or the request is box-prompted, DOTA is skipped unless forced.

## Inputs / Outputs

Outputs are SAM3-shaped `(mask, bbox_xyxy, score, label)` tuples. The service entrypoint tags them with `source_layer="dota_obb"` before NMS and response serialization.

## Failure modes

Partial model-load failure leaves the bundle unloaded and the layer contributes zero candidates. The precision gate intentionally skips non-DOTA prompt sets to avoid unrelated false positives.

## Cross-references

- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [decisions/why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md) — the GDINO+DOTA NMS interaction
- [fusion-and-nms.md](fusion-and-nms.md)
