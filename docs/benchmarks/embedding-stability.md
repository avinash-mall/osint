# Embedding Stability — Augmentation Re-ID on Stills

**Source:** [scripts/embedding_stability.py](../../scripts/embedding_stability.py)
**Dataset:** DOTA chips, augmentation-based intra-instance re-ID
**Layer:** `dinov3_sat`

## What it measures

For each ground-truth instance: generate N augmented crops (random rotation, jitter, color), compute embeddings, check whether the top-1 nearest neighbor in the full pool is the same instance. Reported as top-1 accuracy.

## Result

| Embedding | Top-1 accuracy |
|---|---|
| **DINOv3-SAT-L** | **100%** (on the 8-chip × 15-instance × 4-aug eval) |
| DINOv3-LVD-L (removed) | NaN on small crops — see [decisions/removed-dinov3-lvd.md](../decisions/removed-dinov3-lvd.md) |

The 100% number reflects an internally curated eval; real cross-pass re-ID is more variable. The test is **purpose-built to detect silent failures** (NaN, semantic drift), not to certify a particular quality level.

## How to reproduce

```bash
python scripts/embedding_stability.py \
  --url http://172.18.0.2:8001 \
  --max-chips 8 --max-instances 15 --n-aug 4 --layers dinov3_sat
```

## Cross-references

- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md)
- [decisions/why-dinov3-sat-only.md](../decisions/why-dinov3-sat-only.md)
- [scripts/benchmark-scripts.md](../scripts/benchmark-scripts.md)
