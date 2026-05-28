# `inference-sam3/fusion.py` — Mask-Aware Fusion + WBF / NMS

**Path:** [inference-sam3/fusion.py](../../inference-sam3/fusion.py)
**Lines:** ~440
**Depends on:** `numpy`, `pycocotools` (COCO RLE), `ensemble-boxes` (WBF, optional with NMS fallback)

## Purpose

Merge raw image candidates from SAM3 + DOTA-OBB + FAIR1M-OBB + Grounding-DINO + YOLOE + SAR-CFAR into one deduplicated detection list. Builds the canonical per-detection record (OBB extraction, COCO RLE encoding, overlay-label join), then runs cross-detector fusion — Weighted Boxes Fusion by default, classic mask-aware NMS as the A/B alternative.

## Why this design

- **Mask IoU, not box IoU (NMS path)** — closely spaced objects can have ≥0.5 box IoU but ~0 mask IoU (overlap geometrically, cover different pixels). Mask-aware NMS keeps both.
- **WBF by default (T2.8)** — NMS-based ensembles destroy mAP when overlapping detectors disagree (see [why-wbf-over-nms.md](../decisions/why-wbf-over-nms.md) and the GDINO+DOTA collapse in [why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md)). WBF averages overlapping boxes weighted by per-source trust instead of suppressing the loser.
- **Per-source trust weights tuned on triage set** — DOTA-OBB and FAIR1M-OBB (closed-vocab specialists) at 1.0; SAM3 and YOLOE at 0.5; GDINO at 0.3 because its text-derived boxes drift; SAR-CFAR at 0.7. Operators override via `SAM3_WBF_WEIGHTS` JSON env.
- **Defence-in-depth fallback** — if `ensemble_boxes` import fails, `wbf_fusion` logs a warning and falls back to `mask_aware_nms`. Runner never crashes.
- **Edge-touch detection** — a mask touching the chip edge is marked `edge_truncated=true`; downstream the worker re-stitches at chip boundaries.
- **OBB extraction from mask contour** via `cv2.minAreaRect`. Falls back to HBB when contour area tiny.
- **Overlay labels additive** — Prithvi flood/burn polygons add labels like `"water"` / `"crop:corn"` when their mask × Prithvi overlay IoU exceeds `SAM3_PRITHVI_OVERLAY_THRESHOLD`.

## Key symbols

- [`candidate_to_detection`](../../inference-sam3/fusion.py#L73) — builds one detection dict from a candidate tuple.
- [`mask_to_obb_record`](../../inference-sam3/fusion.py#L102) — `cv2.minAreaRect` on the mask contour → OBB record.
- [`coco_rle`](../../inference-sam3/fusion.py#L137), [`decode_rle`](../../inference-sam3/fusion.py#L143) — base64-COCO-RLE encode/decode.
- [`overlay_labels`](../../inference-sam3/fusion.py#L151) — Prithvi overlay → label list.
- [`mask_aware_nms`](../../inference-sam3/fusion.py#L161) — legacy NMS path; preserved unchanged.
- [`wbf_fusion`](../../inference-sam3/fusion.py#L219) — per-source weighted boxes fusion; stamps `wbf_member_count` + `wbf_member_sources` on survivors.
- [`fuse_detections`](../../inference-sam3/fusion.py#L386) — env-dispatched entry point (`SAM3_FUSION_MODE=wbf|nms`); the call main.py uses.
- [`_wbf_weights`](../../inference-sam3/fusion.py#L34) — merges `SAM3_WBF_WEIGHTS` JSON env overrides on top of `_DEFAULT_WBF_WEIGHTS`.

## Inputs / Outputs

`fuse_detections(detections, *, image_w, image_h, agnostic=False) -> list[dict]`

Each input detection must carry `bbox` (normalized cxcywh), `mask_rle`, `confidence`, `class`, and `source_layer`. Output has the same shape plus, on the WBF path:
- `wbf_member_count: int` — how many input detections fused into this output.
- `wbf_member_sources: list[str]` — sorted unique `source_layer` values that contributed.

`confidence` is overwritten with the WBF-fused score (per-source-weight-rescaled mean for `conf_type="avg"`).

## Failure modes

- **`ensemble_boxes` missing** — caught at `wbf_fusion` import time; logs warning; falls back to `mask_aware_nms` with the same IoU. Verified by `test_wbf_falls_back_to_nms_when_ensemble_boxes_missing`.
- **Degenerate bbox** (zero width/height after normalization) — silently skipped before fusion; never reaches WBF.
- **Soft-NMS knob (`SAM3_NMS_SOFT=1`)** — forces NMS path even when `SAM3_FUSION_MODE=wbf`, because soft-decay is meaningless for an averaging primitive. Main.py honours this in the call-site branch.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SAM3_FUSION_MODE` | `wbf` | `wbf` → `wbf_fusion`; `nms` → `mask_aware_nms` |
| `SAM3_WBF_WEIGHTS` | empty | JSON dict `{source_layer: float}`; merges over defaults |
| `SAM3_WBF_IOU` | `0.55` | IoU threshold for WBF cluster matching |
| `SAM3_WBF_SKIP_THRESHOLD` | `0.05` | Per-input minimum confidence (WBF drops below this) |
| `SAM3_NMS_AGNOSTIC` | `1` | Cross-class dedup (both paths honour this) |
| `SAM3_NMS_SOFT` | `0` | Force NMS path with soft-decay regardless of mode |

## Cross-references

- [sam3-runner-internals.md](sam3-runner-internals.md)
- [prithvi-multispectral.md](prithvi-multispectral.md)
- [why-wbf-over-nms.md](../decisions/why-wbf-over-nms.md) — decision context for WBF default
- [why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md) — NMS-collapse measurement that motivated this
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
- Tests: [inference-sam3/tests/test_fusion.py](../../inference-sam3/tests/test_fusion.py), [inference-sam3/tests/test_fusion_wbf.py](../../inference-sam3/tests/test_fusion_wbf.py)
