# Removed: DINOV3_LVD

## Status

Removed in v0.10.

## What it was

DINOv3 ViT-L pretrained on LVD-1689M (the general image dataset, not the satellite-specific SAT-493M variant). Loaded via the (now-removed) `SAM3_LOAD_DINOV3_LVD` env flag.

## Why it was removed

On real drone-video crops:

- **NaN embeddings.** The forward pass produced `nan` in the embedding tensor for many crops. This was a **silent failure** — the embedding was persisted, downstream cosine similarity used it, and re-ID broke without raising any exception.
- **2.5× slower than DINOV3_SAT.** ~715 ms vs 217 ms on the same hardware.
- **No quality advantage on either modality.** SAT pretraining matches the dominant workload (satellite re-ID); LVD pretraining was strictly worse on stills and unusable on video.

See [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md) for the SEP comparison.

## Why NaN happened

Best guess: numerical instability on very small crops (e.g. <60 px) with non-square aspect ratios. DINOV3_SAT handles the same inputs without issue — possibly because SAT-pretraining sees more degenerate crops during training, possibly because of patch-embedding norm differences.

We did not pursue the root cause. The fix was to remove the path entirely.

## Lesson

- Embedding heads that can return NaN need an explicit check in the pipeline. Persisting a NaN vector is worse than persisting nothing.
- Pretraining domain matters: SAT-493M is qualitatively different from LVD-1689M on satellite chips and drone crops.

## Cross-references

- [why-dinov3-sat-only.md](why-dinov3-sat-only.md)
- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md)
- [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md)
