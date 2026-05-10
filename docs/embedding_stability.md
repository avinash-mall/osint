# Embedding Stability / Re-ID Test

Generated: 2026-05-10T13:53:56
Test corpus: DOTA-v1.0 val (max 15 instances, 4 augmentations per instance)

## Methodology

For each ground-truth bbox we obtain (1 + N) embeddings by:
  - Embedding the original bbox, AND
  - Embedding N augmented bboxes (translation ±10%, scale 0.85–1.15× around the original).

**INTRA** = mean cosine similarity within the same instance (should be ≈ 1.0 for a stable embedding).
**INTER** = mean cosine similarity between different instances (should be lower).
**SEPARATION** = INTRA − INTER (higher is better; useful for re-ID).
**Top-1 retrieval** = for each instance's primary embedding, the nearest neighbour in the embedding pool is one of its own augmented variants (vs. another instance).

## Results

| Layer | Instances | INTRA cos | INTER cos | SEPARATION | Top-1 | Eval ms |
|---|---|---|---|---|---|---|
| dinov3_sat | 15 | 0.921 ± 0.040 | 0.737 ± 0.129 | **+0.185** | 100.0% | 5233 ms/inst |
| dinov3_lvd | — | — | — | — | — | _no successful embeddings_ |

## Interpretation

- **SEPARATION ≥ 0.10**: embedding is useful for object re-ID — same-object pairs are clearly closer than different-object pairs.
- **SEPARATION ≈ 0**: embedding doesn't distinguish instances (useless for tracking).
- **Top-1 ≥ 70%**: nearest-neighbour matching reliably identifies the same object across augmentations.
