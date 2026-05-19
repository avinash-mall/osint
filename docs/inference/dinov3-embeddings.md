# `inference-sam3/embedding.py` — DINOv3-SAT Embedding Head

**Path:** [inference-sam3/embedding.py](../../inference-sam3/embedding.py)
**Lines:** ~57
**Depends on:** `transformers`, weights `facebook/dinov3-vitl16-pretrain-sat493m` (gated)

## Purpose

Compute a 1024-D embedding per detection (cropped to the bbox) for re-ID across chips and frames. The output is base64-encoded fp16 in the `embedding.fp16_b64` field of every detection record.

## Key symbols

- [`_load`](../../inference-sam3/embedding.py#L14) — model + processor loader.
- [`load_sat`](../../inference-sam3/embedding.py#L26) — `facebook/dinov3-vitl16-pretrain-sat493m` wrapper.
- [`embed_crop`](../../inference-sam3/embedding.py#L30) — main entry: `(bundle, image, bbox) -> {model, dim, fp16_b64}`.
- [`dinov3_pool`](../../inference-sam3/embedding.py#L44) — pools patch tokens to a single vector.

## Why DINOv3-SAT, not DINOv3-LVD

See [decisions/why-dinov3-sat-only.md](../decisions/why-dinov3-sat-only.md). The LVD variant produced silent NaN failures on real drone-video crops; SAT pretraining matches the dominant workload.

## Throughput note

Per-detection embedding adds ~217 ms per chip (single GPU). For high-volume chip throughput, set `SAM3_LOAD_DINOV3_SAT=0` — the rest of the pipeline still works, just without re-ID capability.

## Cross-references

- [decisions/why-dinov3-sat-only.md](../decisions/why-dinov3-sat-only.md)
- [decisions/removed-dinov3-lvd.md](../decisions/removed-dinov3-lvd.md)
- [benchmarks/embedding-stability.md](../benchmarks/embedding-stability.md)
- [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md)
- [backend/tracker-fmv.md](../backend/tracker-fmv.md) — consumer of these embeddings
