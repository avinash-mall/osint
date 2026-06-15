# Inference Layer Comparison

Generated: 2026-05-22T18:02:48.024090+00:00  GPU: unknown

## Box Detectors

Dataset: DOTA-v1.0 (30 chips, IoU threshold 0.5)

| Config | mAP@0.5 | Macro F1 | Δ mAP vs SAM3 | Median Total ms | Δ ms vs SAM3 |
|---|---|---|---|---|---|
| sam3 (baseline) | 0.1599 | 0.1480 | — | 4740.9 | — |
| sam3+dota_obb | 0.5100 | 0.4817 | +0.3501 | 5528.8 | +787.9 |
| sam3+grounding_dino | 0.1599 | 0.1480 | +0.0000 | 4627.4 | -113.5 |
| sam3+dota_obb+grounding_dino | 0.4525 | 0.4491 | +0.2926 | 5292.9 | +552.0 |

### Per-class metrics (SAM3 baseline)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|
| aircraft | 0.3473 | 0.4911 | 0.4069 | 0.3887 |
| armored_vehicle | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| battle_damage | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| fortification | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| industrial | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| logistics | 0.1122 | 0.0655 | 0.0827 | 0.0195 |
| military_installation | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| missile_strategic | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| naval | 0.4791 | 0.3801 | 0.4239 | 0.3828 |
| other | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| tactical_vehicle | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| transportation | 0.0352 | 1.0000 | 0.0680 | 0.2361 |
| urban | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

### Per-class metrics (all box detectors)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|
| aircraft | 0.3316 | 0.3846 | 0.3562 | 0.3609 |
| armored_vehicle | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| battle_damage | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| fortification | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| industrial | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| logistics | 0.4323 | 0.4328 | 0.4325 | 0.2156 |
| military_installation | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| missile_strategic | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| naval | 0.4700 | 0.3469 | 0.3992 | 0.3795 |
| other | 0.2669 | 0.2180 | 0.2400 | 0.3247 |
| tactical_vehicle | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| transportation | 0.0355 | 1.0000 | 0.0685 | 0.3194 |
| urban | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## Recommendations

Based on the comparative analysis above.

| Layer | Verdict | Quality impact | Latency cost | Notes |
|---|---|---|---|---|
| DOTA_OBB | ✅ Keep | +0.35 mAP | +788 ms | Adds aerial vehicle/plane classes not in SAM3 vocab |
| ~~GROUNDING_DINO~~ | ❌ Removed (was auto-gated) | +0.29 mAP | +552 ms | Open-vocab recall; was auto-gated when all prompts are in SAM3+DOTA common vocab. Layer since removed. |
| ~~PRITHVI~~ | ❌ Removed 2026-06-12 | — (segmentation) | ? | Flood/burn heads removed — noisy false detections, burn-head chip IoU ≈ 0. See [decisions/removed-prithvi-battle-damage.md](../decisions/removed-prithvi-battle-damage.md) |
| DINOV3_SAT | ✅ Keep for tracking | — (embedding) | ? | Embedding for cross-image object re-ID; see video_tracking_stability.md |
| TERRAMIND | ⚠️ SAR-only | — (embedding) | ? | SAR-only; no impact on RGB/multispectral; enable only for SAR pipelines |
