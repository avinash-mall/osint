# Inference Layer Comparison — Image Stack

**Raw report:** [bench/comparison.md](../../bench/comparison.md), [bench/sam3_comparison.md](../../bench/sam3_comparison.md)
**Source:** [scripts/compare_inference_layers.py](../../scripts/compare_inference_layers.py)
**Datasets:** DOTA-v1.0 val (30 chips, 1619 GT boxes); synthetic S1 GRD
**Hardware:** RTX 5070 Ti, 16 GB

## Headline results

| Layer | Verdict | Quality | Cost / chip | Notes |
|---|---|---|---|---|
| **SAM 3 (base)** | ✅ Foundation | mAP 0.05 alone on DOTA val | 590 ms | Required for masks |
| **DOTA-OBB** | ✅ **Keep** | mAP **0.05 → 0.61** (aircraft recall 0 → 92%, naval 0.6 → 21%) | **+50 ms** | Single biggest quality win |
| ~~Grounding-DINO~~ | ❌ **Removed** (was auto-gated) | +0.01 mAP when forced | +115 ms (skipped 100% on common-vocab prompts) | Layer deleted; was server-side auto-gated |
| **DINOv3-SAT** | ✅ Keep | Top-1 re-ID **100%** on stills, SEP **+0.22** on 1440p drone video | +217 ms / +293 ms embed | Only embedding worth keeping |
| **TerraMind** | ⚠️ SAR-only | Quality unmeasurable without real S1 GRD | **~0 ms** (within noise) | Only fires on `modality=sar` |
| **YOLOE** | ✅ FMV | Replaces SAM3 AMG; emits labels directly | comparable to SAM 3.1 PCS | Both `-pf` and `-seg` |
| ~~Prithvi~~ | ❌ **Removed** (2026-06-12) | Per-pixel flood/burn; noisy false detections, burn-head chip IoU ≈ 0 | — | See [decisions/removed-prithvi-battle-damage.md](../decisions/removed-prithvi-battle-damage.md) |
| ~~DEFENCE_YOLO~~ | ❌ **Removed** | 1297 FPs / 0 TPs as `battle_damage` | — | Actively degraded mAP |
| ~~DINOV3_LVD~~ | ❌ **Removed** | NaN embeddings on drone-video crops | 715 ms (2.5× SAT) | Silent failure on real data |
| ~~SAM3 AMG~~ | ❌ **Removed** | Required Grounding-DINO for labels | — | YOLOE-26x-seg(-pf) replaces it |

## Key finding

**DOTA_OBB alone (mAP 0.61) outperforms DOTA_OBB + GROUNDING_DINO together (mAP 0.11) on common-vocab DOTA prompts** — adding GDINO caused NMS to suppress DOTA's correct detections. An auto-gate mitigated this while the layer was in production; the Grounding-DINO layer has since been removed.

## How to reproduce

See [testing/benchmark-harness.md](../testing/benchmark-harness.md).

## Cross-references

- [scripts/compare-inference-layers.md](../scripts/compare-inference-layers.md)
- [decisions/removed-prithvi-battle-damage.md](../decisions/removed-prithvi-battle-damage.md)
- [decisions/removed-defence-yolo.md](../decisions/removed-defence-yolo.md)
- [decisions/removed-dinov3-lvd.md](../decisions/removed-dinov3-lvd.md)
- [decisions/removed-sam3-amg.md](../decisions/removed-sam3-amg.md)
