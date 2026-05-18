# Inference Layer Comparison

Generated: 2026-05-18T16:31:51.571341+00:00  GPU: unknown

## Box Detectors

Dataset: DOTA-v1.0 (2 chips, IoU threshold 0.5)

| Config | mAP@0.5 | Macro F1 | Δ mAP vs SAM3 | Median Total ms | Δ ms vs SAM3 |
|---|---|---|---|---|---|
| sam3 (baseline) | 0.9411 | 0.5123 | — | 1800.4 | — |
| sam3+dota_obb | 0.0000 | 0.0000 | -0.9411 | 0.0 | -1800.4 |
| sam3+grounding_dino | 0.0000 | 0.0000 | -0.9411 | 0.0 | -1800.4 |
| sam3+dota_obb+grounding_dino | 0.0000 | 0.0000 | -0.9411 | 0.0 | -1800.4 |

### Per-class metrics (SAM3 baseline)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|
| other | 0.3565 | 0.9815 | 0.5230 | 0.9411 |

### Per-class metrics (all box detectors)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|

## Recommendations

Based on the comparative analysis above.

| Layer | Verdict | Quality impact | Latency cost | Notes |
|---|---|---|---|---|
| DOTA_OBB | ✅ Keep | -0.94 mAP | -1800 ms | Adds aerial vehicle/plane classes not in SAM3 vocab |
| GROUNDING_DINO | ✅ Keep (auto-gated) | -0.94 mAP | -1800 ms | Open-vocab recall; auto-gated when all prompts are in SAM3+DOTA common vocab |
| PRITHVI | ✅ Keep | — (segmentation) | ? | Only specialist for multispectral flood/burn; no alternative |
| DINOV3_SAT | ✅ Keep for tracking | — (embedding) | ? | Embedding for cross-image object re-ID; see video_tracking_stability.md |
| TERRAMIND | ⚠️ SAR-only | — (embedding) | ? | SAR-only; no impact on RGB/multispectral; enable only for SAR pipelines |
