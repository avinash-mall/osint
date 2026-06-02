# Decision: optical-inference throughput — batch embeddings, ROI postprocess, multi-GPU back-off floor, non-blocking GD gate

## Context

After the multi-GPU device-mismatch fix, optical `/detect_raw` returned 200 but each tile took 4–16 s and the chip pipeline appeared to stall (~9/16) on a 4× A100 host. Per-tile `sam3_detect_timing` showed SAM3 itself was cheap (~0.5 s); the cost was elsewhere and scaled with the high open-vocabulary detection count (50–173/tile):

- `embedding` 2.4–7.6 s — DINOv3 ran **one detection at a time** (PIL convert + forward + `.cpu()` per crop).
- `postprocess` 2.9–8.8 s — `mask_to_obb_record` ran `np.where` + `cv2.morphologyEx` + `findContours` + `minAreaRect` on the **full 1008×1008 mask per detection**.
- `specialists` 30 ms–12 s spikes — the GroundingDINO gate did a **blocking** ontology fetch (3 sensors × 5 s timeout) on cache miss/expiry, inside the request path.
- "Stuck at 9/16" — the worker's adaptive concurrency back-off **halved its in-flight limit down to 1** whenever p95 > 3×p50, which the naturally high tile-latency variance tripped, feeding only ~1 of the 4 GPUs even though the inference service round-robins all 4 replicas lock-free.

Operator chose a full code fix keeping every feature (embeddings, GroundingDINO, DOTA-OBB, max recall). So all four changes are **result-preserving** — identical detections/outputs, only faster.

## Decision

1. **Batch the DINOv3 embeddings.** New `embedding.embed_crops_batched(bundle, image, bboxes)` collects all in-bounds crops and runs the encoder in batches of `SAM3_EMBED_BATCH_SIZE` (default 32) with one host transfer per batch. `main.py` collects bboxes in the detection loop and makes one batched call after it. Each crop is still encoded independently (the encoder resizes each to a fixed size), so per-crop output is byte-identical to the old `embed_crop`.

2. **ROI-crop the mask postprocess.** `fusion.mask_to_obb_record` derives the mask's own bounding box from cheap 1-D `any(axis)` reductions, pads it by the morphology kernel, and runs the `cv2`/`np.where`/`findContours`/`minAreaRect` work on that ROI instead of the full frame. `minAreaRect` points are offset back to full-image coordinates before normalising → identical OBB output. `_touches_edge`, `coco_rle`, and the area sum stay on the full mask (RLE is full-image; pycocotools encode is C-fast). Per-detection cost goes from O(image) to O(object).

3. **Floor the worker back-off at the replica count.** `worker_legacy.py` floors `_effective_pending_limit` at `INFERENCE_MIN_PENDING_CHIPS` (default 4; set to the inference GPU/replica count) instead of `max(1, …)`, and widens the trigger to p95 > 4×p50. The back-off still protects against genuine memory pressure but can no longer starve the replica pool, so ≥4 tiles stay in flight → all 4 A100s are fed. Pure scheduling; no result change.

4. **Make the GroundingDINO gate's ontology fetch non-blocking.** `grounding_dino_gate._fetch_ontology_vocab` returns the cached vocab immediately and refreshes in a background thread (`_refresh_ontology_vocab`, single-flight, 1.5 s/sensor timeout) when stale. The static vocab still gates common terms during a refresh, so the gate decision is unchanged once warm; only the cold/expired stall (up to 15 s) is removed, which also eliminates the false back-off triggers in (3).

## Why result-preserving (not just faster)

- (1) batching changes only *how many* forwards run, not the math — each crop's CLS vector and fp16 bytes are identical.
- (2) translation-invariance of `minAreaRect` angle/area + the explicit ROI→image point offset makes the OBB identical; padding ≥ kernel keeps `MORPH_OPEN` border behaviour identical to full-frame.
- (3) and (4) are scheduling / caching changes; detection inputs and outputs are untouched.

## Alternatives considered

- **Disable embeddings / GroundingDINO, or raise thresholds** — rejected by the operator (all features + max recall kept). Batching/ROI-cropping deliver the speed without the recall/feature tradeoff.
- **Move the back-off to be replica-aware via a discovery handshake** — overkill; a single floor env tuned to GPU count is enough and stays offline-friendly.
- **Run embeddings/postprocess off the event loop wholesale** — the batched embedding already uses `run_in_threadpool`; rewrapping the fusion loop is a larger change deferred until measured necessary.

## New env vars (auto-set by `configure_host.py`)

These are GPU/VRAM-dependent, so `scripts/configure_host.py` writes them into the generated `.env` block per host — operators do not hand-tune them (the in-code defaults are only fallbacks when the block is absent):

- `SAM3_EMBED_BATCH_SIZE` — crops per DINOv3 forward. **VRAM-tiered** via the profile field `gpu_profiles.sam3_embed_batch_size` (Turing 16; consumer Ampere/Ada/Blackwell 32; datacenter Ampere/A100 64; Hopper / datacenter Blackwell 96). Code default 32.
- `INFERENCE_MIN_PENDING_CHIPS` — adaptive back-off floor. **GPU-count-derived**: `configure_host` sets it to `len(info.gpus)` (one inference replica per GPU). Code default 4.
- `INFERENCE_CHIP_CONCURRENCY` (existing knob) — `configure_host` raises it to `max(profile_baseline, gpu_count)` so the worker's poster pool is at least as wide as the GPU count; otherwise the poster pool caps concurrent POSTs below the floor and the extra GPUs stay idle.
- `SAM3_GPU_MEMORY_FRACTION` — per-process VRAM ceiling (fraction of each GPU's total). **Co-tenant-derived**, not arch/VRAM-tiered: `configure_host` emits it only when it detects another process already holding VRAM at configure time (see the "Follow-up" section below). Code default 0 = no cap. When set, `configure_host` also shrinks `SAM3_EMBED_BATCH_SIZE`/`SAM3_BATCHED_TEXT_CHUNK_SIZE` to frugal values.

See [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md).

## Follow-up: concurrency exposed an unpinned-device crash

Raising concurrency (back-off floor + `INFERENCE_CHIP_CONCURRENCY` = GPU count) surfaced a latent multi-GPU bug: `/detect_raw` started 500-ing with `CUDA error: an illegal memory access was encountered`, first in `embed_crops_batched`, then cascading (a poisoned CUDA context kills every later request until the container restarts).

Cause: GPU forwards that run in the anyio worker threadpool must pin PyTorch's thread-local current CUDA device to the replica's GPU. SAM3's `run_text_prompts` does (`_device_ctx`), but `embed_crops_batched` and `grounding_dino.run`'s forward did an explicit `.to(device)` + forward **without** pinning. Worker threads default to `cuda:0`, so a forward on `cuda:N` issues cross-device cuBLAS/cuDNN kernels and illegal-accesses. It stayed latent while requests were effectively serialized (the old inline per-detection `embed_crop`); 4 concurrent forwards across 4 GPUs trip it.

Fix (part 1 — device pinning): a shared `inference_utils.device_ctx(device)` context manager (mirrors `_device_ctx`) now wraps the GPU work in `embed_crops_batched`, `grounding_dino.run` (`_do_forward`), and `dota_obb.run` (`_do_predict`). Every threadpool forward now pins its replica's device like SAM3.

Fix (part 2 — per-replica serialization): device pinning alone did not fix it — `/detect_raw` still illegal-accessed the moment two requests ran on one replica (`queue_depth=2`). Cause: only SAM3's forward held `bundle["lock"]`; the specialists and the batched embedding ran in the threadpool with **no lock**, so two concurrent requests on the same replica launched matmuls on that device's default-stream **cuBLAS workspace simultaneously** → corruption → illegal access. (Pre-existing latent bug; the old inline per-detection embedding was serialized in the event loop, and the back-off kept in-flight ≈1, so forwards never overlapped. The concurrency bump removed that implicit serialization.) Fix: `_detect_pipeline` wraps every per-replica GPU forward that isn't SAM3 (dota, grounding-dino, prithvi, batched embedding) in a `_locked` helper that acquires `bundle["lock"]` **inside the worker thread** (so it serializes one replica's forwards without stalling the event loop or other replicas). SAM3 keeps its own `with bundle["lock"]`. Result: one replica runs one GPU forward at a time; the 4 replicas still run in parallel. Stopgap while undeployed: `SAM3_DEVICE=cuda:0` **and** `INFERENCE_CHIP_CONCURRENCY=1` (no overlap possible).

## Follow-up: the real residual cause was a vLLM GPU co-tenant

After parts 1+2, the illegal-access still recurred under load — but the diagnosis above (cross-device, same-replica race) was only *part* of it. `/health/memory` plus the `nvidia-smi` process table settled it: the 4× A100 80 GB box is **shared**. A 4-way tensor-parallel **vLLM server holds ~40 GiB/card** (`VLLM::Worker_TP0..3`); inference-sam3 is a single process spanning all 4 cards at ~17 GiB each. torch's own view was clean — ~16 GiB allocated, ~16 GiB reserved, **~0 fragmentation** (`expandable_segments` was already on and working), and it *believed* ~63 GiB was free. But `free = total − reserved` is **blind to other tenants**: only ~22 GiB was actually free. Under a heavy chip, an inference replica's peak (~28–38 GiB) plus vLLM's 40 GiB brushed the 80 GiB ceiling, and a fused cuBLAS/SDPA workspace allocation failed **inside a kernel** — surfacing as a context-poisoning `illegal memory access` rather than a clean `OutOfMemoryError`. That async, allocation-dependent failure is exactly why the "origin" wandered across embedding → grounding-dino → SAM3 between runs.

So the device-pin (part 1) and per-replica lock (part 2) were both correct and necessary, but the *residual* crashes were pure **VRAM contention with a co-tenant**, not a bug in inference code (which is stable and well-behaved).

> **Superseded (auto-config only):** the runtime ceiling below is still in force as a **manual**
> knob, but the `configure_host.py` **auto-derivation** of `SAM3_GPU_MEMORY_FRACTION` was later
> **removed** — it misfired by counting the stack's own replicas as a co-tenant and spuriously
> throttled dedicated cards. See [why-removed-auto-vram-cap.md](why-removed-auto-vram-cap.md).
> Parts 1 (device-pin) and 2 (per-replica lock) are unaffected.

Fix (part 3 — frugal + memory-capped, operator chose this over GPU isolation):
- **Ceiling:** new `SAM3_GPU_MEMORY_FRACTION` env. `main.py:_apply_gpu_memory_fraction` calls `torch.cuda.set_per_process_memory_fraction(frac, device)` per replica at load, so an over-budget allocation raises a catchable `OutOfMemoryError` (absorbed by `inference_utils.safe_predict`/`memory_guard`, graceful per-chip fallback, service stays up) instead of illegal-accessing into the neighbour. Keeping torch well below the physical ceiling also leaves room for untracked kernel-internal workspaces.
- **Frugal peak:** on a co-tenant card the per-chip peak is kept *inside* the cap on the common path by shrinking the two largest activation knobs — `SAM3_EMBED_BATCH_SIZE`→16 and `SAM3_BATCHED_TEXT_CHUNK_SIZE`→8. These are **result-preserving** (fewer crops/prompts per forward, identical outputs), so normal runs stay full-quality; the cap is only the tail safety net.
- **Auto-config:** `configure_host.py` now queries `memory.used` alongside `memory.total`. When a card already shows ≥ `COTENANT_MIN_USED_MIB` (2048) of usage at configure time, it derives `SAM3_GPU_MEMORY_FRACTION = (total − max_used − 4096 MiB) / total` (floored at 0.20) and emits the frugal batch overrides. Dedicated cards (idle at configure time) get no cap and keep the profile's full batch sizes. **Run `configure_host.py` with co-tenants up but the Sentinel stack down**, so `memory.used` reflects only the neighbour.

Honest scope: this converts the catastrophic, context-poisoning crash into at worst a graceful per-chip degradation and keeps the common path full-quality — it **reduces** the crash window sharply but cannot *guarantee* zero failures under an arbitrarily heavy simultaneous vLLM load. Only GPU isolation (dedicating cards) removes contention entirely; the operator declined that to keep all 4 cards shared.

## Cross-references

- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md)
- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md)
- [inference/grounding-dino-gate.md](../inference/grounding-dino-gate.md)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md) — worker chip dispatcher + back-off
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
- [decisions/cached-forward-device-normalise.md](cached-forward-device-normalise.md) — the multi-GPU crash fixed just before this
