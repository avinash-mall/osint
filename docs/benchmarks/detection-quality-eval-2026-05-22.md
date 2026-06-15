# Inference Layer Comparison

Generated: 2026-05-22T17:41:45.381525+00:00  GPU: unknown

## Box Detectors

Dataset: DOTA-v1.0 (30 chips, IoU threshold 0.5)

| Config | mAP@0.5 | Macro F1 | Δ mAP vs SAM3 | Median Total ms | Δ ms vs SAM3 |
|---|---|---|---|---|---|
| sam3 (baseline) | 0.3968 | 0.3300 | — | 549.6 | — |
| sam3+dota_obb | 0.5999 | 0.4849 | +0.2031 | 813.5 | +263.9 |
| sam3+grounding_dino | 0.3968 | 0.3300 | +0.0000 | 541.6 | -8.0 |
| sam3+dota_obb+grounding_dino | 0.4953 | 0.4025 | +0.0985 | 762.2 | +212.6 |

### Per-class metrics (SAM3 baseline)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|
| aircraft | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| battle_damage | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| logistics | 0.7175 | 0.4305 | 0.5381 | 0.5561 |
| naval | 0.4189 | 0.1144 | 0.1797 | 0.0617 |
| other | 0.2480 | 0.3183 | 0.2788 | 0.3975 |
| transportation | 0.0909 | 0.8000 | 0.1633 | 0.5000 |

### Per-class metrics (all box detectors)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|
| aircraft | 1.0000 | 0.0414 | 0.0795 | 0.0364 |
| battle_damage | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| logistics | 0.7623 | 0.6486 | 0.7009 | 0.6058 |
| naval | 0.6000 | 0.2546 | 0.3575 | 0.2589 |
| other | 0.1942 | 0.3495 | 0.2497 | 0.4408 |
| transportation | 0.0851 | 0.8000 | 0.1538 | 0.7000 |

## Recommendations

Based on the comparative analysis above.

| Layer | Verdict | Quality impact | Latency cost | Notes |
|---|---|---|---|---|
| DOTA_OBB | ✅ Keep | +0.20 mAP | +264 ms | Adds aerial vehicle/plane classes not in SAM3 vocab |
| ~~GROUNDING_DINO~~ | ❌ Removed (was auto-gated) | +0.10 mAP | +213 ms | Open-vocab recall; was auto-gated when all prompts are in SAM3+DOTA common vocab. Layer since removed. |
| ~~PRITHVI~~ | ❌ Removed 2026-06-12 | — (segmentation) | ? | Flood/burn heads removed — noisy false detections, burn-head chip IoU ≈ 0. See [decisions/removed-prithvi-battle-damage.md](../decisions/removed-prithvi-battle-damage.md) |
| DINOV3_SAT | ✅ Keep for tracking | — (embedding) | ? | Embedding for cross-image object re-ID; see video_tracking_stability.md |
| TERRAMIND | ⚠️ SAR-only | — (embedding) | ? | SAR-only; no impact on RGB/multispectral; enable only for SAR pipelines |
