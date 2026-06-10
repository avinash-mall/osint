# Why LAE-DINO cross-chip batching is opt-in (and a no-op on serialize hosts)

**Status:** accepted (implemented, OFF by default)
**Date:** 2026-06-10
**Scope:** `inference-lae/app.py`, `inference-sam3/main.py` (`/detect_batch_raw`), `inference-sam3/grounding_dino.py` (`run_batch`), `backend/worker_legacy.py`

## Decision

Add an opt-in, **global** batching path (`SENTINEL_ENABLE_BATCHING`,
`INFERENCE_LAE_BATCH_SIZE`) that groups raw RGB chips and runs the grounding_dino
(LAE-DINO) call **once per group** as one batched mmdet forward, instead of one
sidecar request per chip. SAM3/DOTA still run per chip under the serialize lock;
only the LAE leg batches. **OFF by default.**

## How it works

- Worker (`worker_legacy.py`) accumulates raw chips into `INFERENCE_LAE_BATCH_SIZE`
  groups and POSTs `/detect_batch_raw` (partial batch flushed at pass end).
- inference-sam3 `/detect_batch_raw`: gate grounding_dino once for the shared
  prompt set → one `grounding_dino.run_batch(...)` over all N chips → then the
  existing per-chip `_detect_pipeline` with `precomputed_gd` so it skips its own
  LAE call. The batched LAE forward is held under `_detect_serial_lock` (it may
  share a GPU with a SAM3 replica; an un-serialized 4-image LAE forward overlaps
  SAM3 and spikes VRAM).
- inference-lae `/detect_batch`: N images → one `DetInferencer(batch_size=N)`
  forward (mmdet broadcasts the shared caption) → N detection lists.

The per-chip and single-chip `/detect_raw` paths are untouched.

## Why this design (and why it stays OFF by default)

Batching only pays off when forwards can run **concurrently**. On the reference
host (A100 + cu13x) `SAM3_SERIALIZE_FORWARDS=1` is mandatory for the
cross-replica poison fix — it serializes *every* GPU forward process-wide. With
that lock, grouping chips can't unlock parallelism; it only front-loads the LAE
call and reduces the per-chip HTTP pipelining that the per-chip poster already
exploits.

**Measured on 4× A100 (SAM3 2-replica + LAE shared, 25-chip pass):**
non-batched **~90 s** vs batched **~130 s**. Batching was *slower*. (The ~49 GB
VRAM spike seen on a couple of chips is pre-existing SAM3 behavior — the same
`49481 MiB` appears in non-batched runs — not caused by batching.)

So batching is kept as a **functional, validated facility** (7 batched forwards,
identical detection counts) for hosts where forwards are **not** serialized — a
truly dedicated LAE GPU with `SAM3_SERIALIZE_FORWARDS=0`, or non-A100/cu13x
hardware once that constraint is lifted. It must not be enabled on a
serialize-forwards host. The real win on this class of host came from the GPU
division + Fix B, not batching.

## Cross-references

- [inference/lae-dino-sidecar.md](../inference/lae-dino-sidecar.md)
- [inference/grounding-dino-detector.md](../inference/grounding-dino-detector.md)
- [decisions/why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md)
- [decisions/why-auto-gpu-division.md](why-auto-gpu-division.md)
