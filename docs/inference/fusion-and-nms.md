# `inference-sam3/fusion.py` — Mask-Aware Fusion + NMS

**Path:** [inference-sam3/fusion.py](../../inference-sam3/fusion.py)
**Lines:** ~255
**Depends on:** `numpy`, `pycocotools` (COCO RLE)

## Purpose

Merge raw image candidates from SAM3 + DOTA-OBB + Grounding-DINO into one deduplicated detection list. Builds the canonical per-detection record: OBB extraction, COCO RLE encoding, overlay-label join.

## Why this design

- **Mask IoU, not box IoU** — closely spaced objects can have ≥0.5 box IoU but ~0 mask IoU (overlap geometrically, cover different pixels). Mask-aware NMS keeps both.
- **Edge-touch detection** — a mask touching the chip edge is marked `edge_truncated=true`; downstream the worker re-stitches at chip boundaries.
- **OBB extraction from mask contour** via `cv2.minAreaRect`. Falls back to HBB when contour area tiny.
- **Overlay labels additive** — Prithvi flood/burn polygons add labels like `"water"` / `"crop:corn"` when their mask × Prithvi overlay IoU exceeds `SAM3_PRITHVI_OVERLAY_THRESHOLD`.

## Key symbols

- [`candidate_to_detection`](../../inference-sam3/fusion.py#L32) — builds one detection dict from a candidate tuple.
- [`mask_to_obb_record`](../../inference-sam3/fusion.py#L61) — `cv2.minAreaRect` on the mask contour → OBB record.
- [`coco_rle`](../../inference-sam3/fusion.py#L96), [`decode_rle`](../../inference-sam3/fusion.py#L102) — base64-COCO-RLE encode/decode.
- [`overlay_labels`](../../inference-sam3/fusion.py#L110) — Prithvi overlay → label list.
- [`mask_aware_nms`](../../inference-sam3/fusion.py#L120) — the actual NMS.
- [`_normalize_obb_points`](../../inference-sam3/fusion.py#L173), [`_hbb_fallback`](../../inference-sam3/fusion.py#L180), [`_touches_edge`](../../inference-sam3/fusion.py#L192), [`_iou`](../../inference-sam3/fusion.py#L199).

## Cross-references

- [sam3-runner-internals.md](sam3-runner-internals.md)
- [prithvi-multispectral.md](prithvi-multispectral.md)
- Tests: [inference-sam3/tests/test_fusion.py](../../inference-sam3/tests/test_fusion.py)
