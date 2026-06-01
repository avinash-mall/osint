# `inference-sam3/embedding.py` — DINOv3-SAT Embedding Head

**Path:** [inference-sam3/embedding.py](../../inference-sam3/embedding.py)
**Lines:** ~125
**Depends on:** `transformers`, weights `facebook/dinov3-vitl16-pretrain-sat493m` (gated)

## Purpose

Compute a 1024-D embedding per detection (cropped to the bbox) for re-ID across chips + frames. Output = base64-encoded fp16 in the `embedding.fp16_b64` field of every detection record.

## Key symbols

- [`_load`](../../inference-sam3/embedding.py#L17) — model + processor loader.
- [`load_sat`](../../inference-sam3/embedding.py#L29) — `facebook/dinov3-vitl16-pretrain-sat493m` wrapper.
- [`embed_crop`](../../inference-sam3/embedding.py#L33) — single-crop entry: `(bundle, image, bbox) -> {model, dim, fp16_b64}`.
- [`embed_crops_batched`](../../inference-sam3/embedding.py#L47) — **batched** entry: `(bundle, image, [bbox,…]) -> [{…},…]`. Collects all in-bounds crops and runs the encoder in batches of `SAM3_EMBED_BATCH_SIZE`, one host transfer per batch. Per-crop output is identical to `embed_crop`; the detect pipeline uses this so N detections cost ~ceil(N/B) forwards, not N. Degenerate (<4 px) crops get the dim-0 placeholder in place.
- [`dinov3_pool`](../../inference-sam3/embedding.py#L111) — pools patch tokens to a single vector (single-image path).

## Why DINOv3-SAT, not DINOv3-LVD

See [decisions/why-dinov3-sat-only.md](../decisions/why-dinov3-sat-only.md). LVD variant produced silent NaN failures on real drone-video crops; SAT pretraining matches the dominant workload.

## Throughput note

Embeddings are computed **batched** once per chip via `embed_crops_batched` (one encoder pass over all detection crops in batches of `SAM3_EMBED_BATCH_SIZE`, default 32), not one forward per detection — the original per-detection loop cost ~44 ms × N detections (2.4–7.6 s/chip on chips with 50–173 detections). `SAM3_EMBED_BATCH_SIZE` bounds VRAM per batch. To skip embeddings entirely (no re-ID), set `SAM3_LOAD_DINOV3_SAT=0` or `SAM3_EMBED_DETECTIONS=0`; the rest of the pipeline is unaffected. See [decisions/optical-inference-throughput.md](../decisions/optical-inference-throughput.md).

## Cross-references

- [decisions/why-dinov3-sat-only.md](../decisions/why-dinov3-sat-only.md)
- [decisions/removed-dinov3-lvd.md](../decisions/removed-dinov3-lvd.md)
- [benchmarks/embedding-stability.md](../benchmarks/embedding-stability.md)
- [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md)
- [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md) — consumer of these embeddings
