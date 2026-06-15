# `inference-sam3/dota_obb.py` — DOTA-OBB Specialist

**Path:** [inference-sam3/dota_obb.py](../../inference-sam3/dota_obb.py)
**Lines:** ~149
**Depends on:** `ultralytics`, env `DOTA_OBB_MODEL_ID`, `DOTA_OBB_THRESHOLD`, `DOTA_OBB_IOU`, `DOTA_OBB_IMGSZ`

## Purpose

Closed-vocabulary oriented-bounding-box detector for DOTA-style overhead objects. Emits SAM3-shaped candidates so the shared fusion path can combine OBB specialist detections with SAM3 masks.

## Why this design

Default checkpoint `yolo26m-obb.pt`, with `yolo11n-obb.pt` still selectable via `DOTA_OBB_MODEL_ID` for low-VRAM hosts. OBB detection stays relevance-gated — prior benchmarks showed indiscriminate specialist competition can damage mAP; see [why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md).

## Key symbols

- [`load`](../../inference-sam3/dota_obb.py#L34-L60) — loads configured Ultralytics OBB checkpoint, applies YOLO optimizations.
- [`run`](../../inference-sam3/dota_obb.py#L62-L119) — runs one chip → `(mask, bbox_xyxy, score, label)` tuples.
- [`_polygon_mask`](../../inference-sam3/dota_obb.py#L121-L138) — OBB corners → boolean polygon mask.
- [`model_versions`](../../inference-sam3/dota_obb.py#L140-L149) — reports loaded model id, threshold, image size.

## Inputs / Outputs

Input: RGB uint8 chip. Output candidates tagged by `main.py` with `source_layer="dota_obb"` before fusion, verifier scoring, backend calibration, evidence ranking.

## Failure modes

Missing Ultralytics / missing checkpoint → unloaded bundle; layer contributes zero candidates. Inference errors logged, return empty list for that chip.

## Cross-references

- [inference/main-app-entrypoint.md](main-app-entrypoint.md)
- [fusion-and-nms.md](fusion-and-nms.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)
