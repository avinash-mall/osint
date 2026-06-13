# Why WBF Replaced NMS for Cross-Detector Fusion

## Problem

Sentinel runs multiple box-emitting detectors against the same chip: SAM3,
DOTA-OBB, LAE-DINO through the `grounding_dino` layer, MVRSD, YOLOE, and SAR
CFAR. They disagree. NMS resolves disagreement by *suppressing* the loser; when
the loser had the better geometry but a slightly lower score, mAP collapses.

We measured this in [why-grounding-dino-auto-gated.md](why-grounding-dino-auto-gated.md): forcing GDINO alongside DOTA-OBB on DOTA-v1.0 val dropped mAP from **0.61 → 0.11**. NMS kept GDINO's lower-confidence box and discarded DOTA-OBB's correct one. The fix at the time was a *gate* (skip GDINO on common-vocab prompts). That's a workaround. The real problem is the fusion primitive.

## Research

- **Weighted Boxes Fusion** (Solovyev et al., 2019) — instead of suppressing the lower-confidence overlap, *average* the coordinates and scores of all overlapping boxes, weighted by per-source trust. On ensemble setups WBF reliably beats NMS / Soft-NMS / NMW by 1.5-5% mAP.
- The `ensemble-boxes` implementation is pure Python, no GPU dependency, and is bundled into the image for air-gap use.
- Our own ensemble disagreement evidence (DOTA→GDINO mAP collapse) shows the NMS primitive is the bottleneck, not the detectors.

## Decision

Adopt WBF as the default cross-detector fusion primitive. Keep NMS as an A/B knob.

- `SAM3_FUSION_MODE=wbf` (default) → [`fusion.fuse_detections`](../../inference-sam3/fusion.py) dispatches to `wbf_fusion`.
- `SAM3_FUSION_MODE=nms` → dispatches to the legacy `mask_aware_nms`.
- `SAM3_NMS_SOFT=1` forces NMS regardless of mode (Soft-NMS is meaningless for the WBF primitive — WBF averages, NMS decays).

**Per-source default weights** (triage-set tuning, T2.8):

| Source layer | Weight |
|---|---|
| `sam3` (open-vocab masks) | 0.5 |
| `dota_obb` (closed-vocab common) | 1.0 |
| `grounding_dino` (LAE-DINO open-vocab text-to-box) | 0.3 |
| `yoloe` (FMV prompt-free) | 0.5 |
| `sar_cfar` (SAR ship detector) | 0.7 |
| `mvrsd` (military-vehicle RGB specialist) | 1.0 |

Operators override via `SAM3_WBF_WEIGHTS='{"grounding_dino": 0.9}'`.

The intuition: DOTA-OBB and MVRSD are closed-vocab specialists trained on
rigorously labelled benchmarks; their box geometry is the most trustworthy
signal we have. SAM3 is broader but its masks are coarser. LAE-DINO's
text-derived boxes can still drift; weight 0.3 keeps them as a *vote*, not a
*veto*.

## What is NOT done

- **WBF inside each detector.** Intra-detector NMS (Ultralytics' per-class NMS inside DOTA-OBB, etc.) stays — those models are tuned with their own NMS, and replacing it would mean retraining.
- **GPU-accelerated WBF.** The pure-Python reference implementation runs at sub-millisecond per chip on the ensemble sizes we see (≤200 boxes per chip). No measurable need for a CUDA kernel.
- **Per-class weight overrides.** The plan called out the possibility ("per-class weighting can override"). Not implemented in T2.8 — the per-source weight already captures most of the variance the triage set showed. Defer until a measurement says otherwise.

## Measured impact

Pending triage-set re-run after T3 (benchmark refresh). Expected lift per the WBF literature: **+3-5% macro F1** over NMS on 4-detector ensembles. The DOTA→GDINO mAP collapse should become a price-adjustment (averaging) rather than a collapse (suppression).

## Defence-in-depth: fallback path

If `ensemble_boxes` import fails for any reason (bake glitch, transient FS issue), [`wbf_fusion`](../../inference-sam3/fusion.py) logs a warning and falls back to `mask_aware_nms` at the same IoU. The runner does not crash. Verified by `test_wbf_falls_back_to_nms_when_ensemble_boxes_missing` in [test_fusion_wbf.py](../../inference-sam3/tests/test_fusion_wbf.py).

## Cross-references

- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md) — module doc with current API
- [why-grounding-dino-auto-gated.md](why-grounding-dino-auto-gated.md) — the NMS-collapse evidence that motivated this
- [benchmarks/detection-quality-eval-2026-05-22.md](../benchmarks/detection-quality-eval-2026-05-22.md) — pre-WBF baseline
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md) — `SAM3_FUSION_MODE`, `SAM3_WBF_WEIGHTS`, `SAM3_WBF_IOU`, `SAM3_WBF_SKIP_THRESHOLD`
