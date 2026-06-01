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

See [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md).

## Cross-references

- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md)
- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md)
- [inference/grounding-dino-gate.md](../inference/grounding-dino-gate.md)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md) — worker chip dispatcher + back-off
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
- [decisions/cached-forward-device-normalise.md](cached-forward-device-normalise.md) — the multi-GPU crash fixed just before this
