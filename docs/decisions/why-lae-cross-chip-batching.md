# Why cross-chip LAE-DINO batching was evaluated and rejected

**Status:** rejected (implemented, benchmarked, removed)
**Date:** 2026-06-10
**Scope:** would have touched `inference-lae/app.py`, `inference-sam3/main.py`, `inference-sam3/grounding_dino.py`, `backend/worker_legacy.py`

## Decision

A full end-to-end cross-chip batching path was built and benchmarked, then
**removed**. It groups raw RGB chips and runs the grounding_dino (LAE-DINO) call
once per group as one batched mmdet forward (instead of one sidecar request per
chip). On the reference host it was **consistently slower** than the per-chip
baseline, so the code was reverted. This doc records the finding so it isn't
re-attempted without new hardware.

## Why it doesn't help (the constraint)

Batching only pays off when forwards run **concurrently**. On A100 + cu13x
`SAM3_SERIALIZE_FORWARDS=1` is mandatory (the cross-replica CUDA-poison fix) and
serializes every GPU forward process-wide, so grouping can't unlock parallelism.
The serialize lock also bounds intra-card VRAM by stopping a chip's SAM3 forward
overlapping the previous chip's DINOv3 embedding on the same card.

Attempting to "free" concurrency by isolating LAE to its own card + dropping
serialize forces SAM3 to a **single replica** (else the poison returns), and
single-replica SAM3 is the bottleneck — and without serialize the
forward/embedding overlap spikes VRAM to ~49 GB and thrashes.

**Measured (4× A100, single 25-chip San Diego pass):**

| Config | Time |
|---|---|
| 2-replica SAM3 + LAE shared + serialize ON + no batch (baseline) | **~90 s** |
| 2-replica + LAE shared + serialize ON + batch | ~130 s |
| 1-replica + LAE dedicated + serialize ON + batch | ~156 s |
| 1-replica + LAE dedicated + serialize OFF + batch | ~170 s |

Every batching/isolation variant was slower. On a 2-GPU host *fast* requires
multi-replica SAM3 (needs serialize); *batching* needs concurrency (needs
serialize off → single replica → bottleneck). They are mutually exclusive here.

## What was kept

The two changes from the same investigation that **do** help/are neutral were
kept: the automated GPU division ([why-auto-gpu-division.md](why-auto-gpu-division.md),
which protects SAM3 replicas) and the embedding decouple
([why-embeddings-not-layer-gated.md](why-embeddings-not-layer-gated.md), Fix B).

## When to revisit

Only on hardware where forwards are not serialized — i.e. SAM3 multi-replica is
safe without `SAM3_SERIALIZE_FORWARDS` (non-A100/cu13x, or once that driver bug
is fixed) AND inference-lae has a genuinely dedicated card. Until then, batching
is a net loss and stays out of the tree.

## Cross-references

- [decisions/why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md)
- [decisions/why-auto-gpu-division.md](why-auto-gpu-division.md)
- [inference/lae-dino-sidecar.md](../inference/lae-dino-sidecar.md)
