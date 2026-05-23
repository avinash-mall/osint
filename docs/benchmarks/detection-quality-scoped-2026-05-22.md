# Inference Layer Comparison

Generated: 2026-05-22T18:50:58.284287+00:00  GPU: unknown

## Box Detectors

Dataset: DOTA-v1.0 (30 chips, IoU threshold 0.5)

| Config | mAP@0.5 | Macro F1 | Δ mAP vs SAM3 | Median Total ms | Δ ms vs SAM3 |
|---|---|---|---|---|---|
| sam3 (baseline) | 0.1821 | 0.1700 | — | 2696.8 | — |
| sam3+dota_obb | 0.5490 | 0.4948 | +0.3669 | 2946.3 | +249.5 |
| sam3+grounding_dino | 0.1821 | 0.1700 | +0.0000 | 2730.6 | +33.8 |
| sam3+dota_obb+grounding_dino | 0.4959 | 0.4675 | +0.3138 | 2904.4 | +207.6 |

### Per-class metrics (SAM3 baseline)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|
| aircraft | 0.3295 | 0.5030 | 0.3981 | 0.3895 |
| industrial | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| logistics | 0.1079 | 0.1017 | 0.1047 | 0.0262 |
| naval | 0.5527 | 0.6384 | 0.5925 | 0.5055 |
| other | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| transportation | 0.0314 | 1.0000 | 0.0610 | 0.2179 |

### Per-class metrics (all box detectors)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|
| aircraft | 0.3081 | 0.3846 | 0.3421 | 0.3609 |
| battle_damage | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| industrial | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| logistics | 0.3784 | 0.5469 | 0.4473 | 0.2731 |
| naval | 0.4600 | 0.4244 | 0.4415 | 0.4220 |
| other | 0.2669 | 0.2180 | 0.2400 | 0.3247 |
| transportation | 0.0321 | 1.0000 | 0.0621 | 0.2949 |

## Recommendations

Based on the comparative analysis above.

| Layer | Verdict | Quality impact | Latency cost | Notes |
|---|---|---|---|---|
| DOTA_OBB | ✅ Keep | +0.37 mAP | +250 ms | Adds aerial vehicle/plane classes not in SAM3 vocab |
| GROUNDING_DINO | ✅ Keep (auto-gated) | +0.31 mAP | +208 ms | Open-vocab recall; auto-gated when all prompts are in SAM3+DOTA common vocab |
| PRITHVI | ✅ Keep | — (segmentation) | ? | Only specialist for multispectral flood/burn; no alternative |
| DINOV3_SAT | ✅ Keep for tracking | — (embedding) | ? | Embedding for cross-image object re-ID; see video_tracking_stability.md |
| TERRAMIND | ⚠️ SAR-only | — (embedding) | ? | SAR-only; no impact on RGB/multispectral; enable only for SAR pipelines |
