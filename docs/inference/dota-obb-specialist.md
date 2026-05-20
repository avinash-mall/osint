# `inference-sam3/dota_obb.py` — DOTA-OBB Specialist

**Path:** [inference-sam3/dota_obb.py](../../inference-sam3/dota_obb.py)
**Lines:** ~149
**Depends on:** `ultralytics`, env `DOTA_OBB_MODEL_ID`, `DOTA_OBB_THRESHOLD`, `DOTA_OBB_IOU`, `DOTA_OBB_IMGSZ`

## Purpose

Closed-vocabulary oriented-bounding-box detector for DOTA-style overhead objects. It emits SAM3-shaped candidates so the shared fusion path can combine OBB specialist detections with SAM3/GDINO masks.

## Why this design

The default checkpoint is now `yolo26m-obb.pt`, with `yolo11n-obb.pt` still selectable through `DOTA_OBB_MODEL_ID` for low-VRAM hosts. OBB detection remains relevance-gated because prior benchmarks showed that indiscriminate specialist competition can damage mAP; see [why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md) and [why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md).

## Key symbols

- [`load`](../../inference-sam3/dota_obb.py#L34-L60) — loads the configured Ultralytics OBB checkpoint and applies YOLO optimizations.
- [`run`](../../inference-sam3/dota_obb.py#L62-L119) — runs one chip and returns `(mask, bbox_xyxy, score, label)` tuples.
- [`_polygon_mask`](../../inference-sam3/dota_obb.py#L121-L138) — converts OBB corners to a boolean polygon mask.
- [`model_versions`](../../inference-sam3/dota_obb.py#L140-L149) — reports loaded model id, threshold, and image size.

## Inputs / Outputs

Input is an RGB uint8 chip. Output candidates are tagged by `main.py` with `source_layer="dota_obb"` before fusion, verifier scoring, backend calibration, and evidence ranking.

## Failure modes

Missing Ultralytics or missing checkpoint returns an unloaded bundle; the layer contributes zero candidates. Inference errors are logged and return an empty list for that chip.

## Cross-references

- [inference/main-app-entrypoint.md](main-app-entrypoint.md)
- [fusion-and-nms.md](fusion-and-nms.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)
