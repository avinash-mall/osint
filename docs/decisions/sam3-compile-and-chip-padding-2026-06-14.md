# SAM3 throughput: torch.compile + decoder-batch + fixed-size chip padding

**Status:** shipped (2026-06-14). Cuts per-chip SAM3 latency ~5–6× on the
Blackwell/cu130 stack with **zero quality change** (same weights). Env:
`SAM3_COMPILE_IMAGE=1`, `SAM3_BATCHED_TEXT_CHUNK_SIZE=64`,
`INFERENCE_PAD_CHIPS_TO_SIZE=1`. Code:
[inference-sam3/main.py](../../inference-sam3/main.py) (`_warmup_image_compile`),
[backend/worker_legacy.py](../../backend/worker_legacy.py) (`INFERENCE_PAD_CHIPS_TO_SIZE`).

## Problem

SAM3 ran as a ~133-concept open-vocab detector at ~2.0–2.7 s/chip on one RTX 5070
Ti, ~83 % of it in the per-concept text-decode loop. A full 15104² scene (~2000
chips) took ~90 min. Fast alternatives were ruled out: generic OVD (YOLOE) is ~52×
faster but mAP@0.5 = 0.002 on aerial (vs SAM3 0.229) — quality-destroying; LAE-DINO
can't run on sm_120 (see [removed-grounding-dino-lae.md](removed-grounding-dino-lae.md)).
So the fix had to be on SAM3 itself, at zero quality cost.

## What changed (each measured, no quality change)

1. **`torch.compile` of the SAM3 image+decode graph (`SAM3_COMPILE_IMAGE=1`,
   default was 0).** On this torch 2.10 / cu130 / Blackwell stack it fuses the
   DETR decoder: decode `sam3_batched_forward` **~1450 → 188 ms (7.7×)**, total/chip
   **~1900 → ~321 ms (~6×)**. Same weights → DOTA mAP@0.5 unchanged at 0.229. It was
   off by default because pre-Blackwell stacks saw little benefit and a long warmup.
2. **Decoder batch size (`SAM3_BATCHED_TEXT_CHUNK_SIZE` 8 → 64).** The decode loop
   batches concepts; larger batches use the GPU better: 8 → 17.6 ms/prompt, 32 →
   13.3, 128 → 10.9. 64 captures most of the win with safe VRAM headroom.
3. **Fixed-size chip padding (`INFERENCE_PAD_CHIPS_TO_SIZE=1`).** `torch.compile`
   specialises on input shape. Interior grid chips are exactly `chip_size²` and hit
   the compiled graph; variable-size **edge** chips (last grid row/col) miss it
   (eager/recompile). The worker now pads edge RGB chips up to `chip_size²` (black,
   bottom-right) and grows the normalization basis to `chip_size`; the padded region
   is marked invalid in the chip's `valid_mask` so any detection there is clipped —
   **georef is unaffected** (window origin/transform unchanged). RGB/optical only;
   MSI/SAR keep their GeoTIFF band path.
4. **Startup warmup (`_warmup_image_compile`).** The first compiled inference pays a
   ~38 s trace+compile. The lifespan preload now runs one dummy 1008² inference at
   startup (gated on `SAM3_COMPILE_IMAGE`, best-effort) so the first real `/detect`
   is already fast and the warmup shape matches the worker's padded chips.

## Result

Interior chips **~2687 ms → ~321 ms (~8×)**; whole scene ~5–6× (the ~90-min 15104²
scene → ~15 min), DOTA mAP unchanged. Quality verified by re-running the DOTA eval
with compile on (identical 0.229).

## Why not the alternatives

- **Swap detector:** every Blackwell-runnable fast OVD collapses on aerial zero-shot
  (measured). The only fast-AND-accurate path is fine-tuning a fast detector on RS
  data — a training project, deferred.
- **Multi-concept single forward:** SAM3 has no native multi-concept path; the
  decode is genuinely O(concepts). Batching (#2) helps GPU utilisation but doesn't
  remove the work; compile (#1) is the large lever.

## Cross-references

- [inference/sahi-equivalence.md](../inference/sahi-equivalence.md) — chip-pass system the compile win rides on
- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md) — `SAM3_COMPILE_IMAGE`, batched text decode
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md) — chipping step
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
