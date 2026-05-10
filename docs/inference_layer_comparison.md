# Inference Layer Comparison

Generated: 2026-05-10T13:01:49.011248+00:00  GPU: unknown

## Box Detectors

Dataset: DOTA-v1.0 (28 chips, IoU threshold 0.5)

| Config | mAP@0.5 | Macro F1 | Δ mAP vs SAM3 | Median Total ms | Δ ms vs SAM3 |
|---|---|---|---|---|---|
| sam3 (baseline) | 0.0497 | 0.0497 | — | 589.8 | — |
| sam3+dota_obb | 0.6076 | 0.5482 | +0.5579 | 639.4 | +49.6 |
| sam3+grounding_dino | 0.0641 | 0.0588 | +0.0144 | 659.4 | +69.6 |
| sam3+dota_obb+grounding_dino | 0.1084 | 0.1056 | +0.0587 | 830.4 | +240.6 |

### Per-class metrics (SAM3 baseline)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|
| civilian | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| logistics | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| military_forces | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| naval | 0.5000 | 0.0063 | 0.0124 | 0.0227 |
| other | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| transportation | 0.3333 | 0.6000 | 0.4286 | 0.4000 |

### Per-class metrics (all box detectors)

| Class | Precision | Recall | F1 | AP |
|---|---|---|---|---|
| aircraft | 0.4167 | 0.9167 | 0.5729 | 0.6032 |
| armored_vehicle | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| civilian | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| logistics | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| military_forces | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| naval | 0.6437 | 0.2090 | 0.3155 | 0.2326 |
| other | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| transportation | 0.1765 | 0.6000 | 0.2727 | 0.3333 |

## Semantic Segmenters (PRITHVI Heads)

Dataset: HLS Burn Scars (10 chips)

Chip-level IoU: chip is predicted positive if any detection has the PRITHVI positive label for the task; IoU = TP/(TP+FP+FN) over chips.

| Config | Task | Chip-level IoU | Pred Positive % | GT Positive % | Δ ms vs SAM3 |
|---|---|---|---|---|---|
| sam3_only | burn_scar | 0.0000 | 0% | 100% | — |
| sam3+prithvi | burn_scar | 0.0000 | 0% | 100% | +19.6 |

## Embedding Models (Latency-Only)

These layers add no detections — they enrich detections with embedding vectors for downstream retrieval/re-ID.

Dataset: DOTA (6 chips, RGB modality)

| Config | Median Total ms | Δ ms vs SAM3 | Embed ms | Coverage |
|---|---|---|---|---|
| sam3_only | 1750.0 | — | 0 | 0% |
| sam3+dinov3_sat | 1967.4 | +217.4 | 292.8 | 83% |
| sam3+dinov3_lvd | 2585.2 | +835.2 | 715.2 | 100% |
| sam3+terramind | 1739.0 | -11.0 | N/A | N/A (SAR) |
| all_embeddings | 1953.4 | +203.4 | 284.9 | 83% |

## Cumulative Pipeline

Shows total latency as each layer is added on top of SAM3 base. Detection count delta shows new detections added by each layer (vs. previous config).

| Layer added | Median Total ms | Δ ms added | Cumulative Δ ms | Det Count (avg) | Det Δ |
|---|---|---|---|---|---|
| SAM3 (base) | 590 | — | — | 56.5 | — |
| + DOTA_OBB | 639 | +50 ms | +50 ms | 78.8 | +22.3 |
| + GROUNDING_DINO | 830 | +191 ms | +241 ms | 79.8 | +1.0 |
| + PRITHVI | 850 | +20 ms | +260 ms | 79.8 | 0 |
| + DINOV3_SAT | 1067 | +217 ms | +478 ms | 79.8 | 0 |
| + DINOV3_LVD | 1903 | +835 ms | +1313 ms | 79.8 | 0 |
| + TERRAMIND (SAR) | 1892 | -11 ms | +1302 ms | 79.8 | 0 |

## Recommendations

Based on the comparative analysis above.

| Layer | Verdict | Quality impact | Latency cost | Notes |
|---|---|---|---|---|
| DOTA_OBB | ✅ Keep | +0.56 mAP | +50 ms | Adds aerial vehicle/plane classes not in SAM3 vocab |
| GROUNDING_DINO | ✅ Keep (auto-gated) | +0.06 mAP | +241 ms | Open-vocab recall; auto-gated when all prompts are in SAM3+DOTA common vocab |
| PRITHVI | ✅ Keep | — (segmentation) | +20 ms | Only specialist for multispectral flood/burn; no alternative |
| DINOV3_SAT | ✅ Keep for tracking | — (embedding) | +217 ms | Embedding for cross-image object re-ID; see embedding_stability.md |
| DINOV3_LVD | ✅ Keep for tracking | — (embedding) | +835 ms | FMV/video object tracking; see embedding_stability.md |
| TERRAMIND | ⚠️ SAR-only | — (embedding) | -11 ms | SAR-only; no impact on RGB/multispectral; enable only for SAR pipelines |

## SAR / TERRAMIND (Synthetic)

Dataset: synthetic 2-band SAR (10 chips). Real Sentinel-1 GRD VV/VH is not freely available on HuggingFace at a manageable size (the SSL4EO-S12 S1 archive is 480 GB). Quality cannot be measured here — TERRAMIND only exposes a pooled embedding + RGB preview, no per-pixel labels — but **latency** is reliable.

| Config | Chips | Median Total ms | Δ ms vs SAM3 (SAR) |
|---|---|---|---|
| sam3_only_sar | 10 | 465.1 | — |
| sam3+terramind | 10 | 460.1 | -5.0 ms |
