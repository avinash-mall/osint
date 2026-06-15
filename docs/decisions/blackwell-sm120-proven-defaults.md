# blackwell_sm120: bake proven defaults into the profile (cu130, compile, recall_review, chunk 64)

**Status:** shipped (2026-06-15). Updates the consumer-Blackwell (RTX 50-series,
sm_120) profile in [scripts/gpu_profiles.py](../../scripts/gpu_profiles.py) so a
fresh `configure_host.py` regen emits the values that were measured-good on real
RTX 5070 Ti hardware, instead of conservative/experimental placeholders the
operator had to hand-override in `.env`.

## Problem

`blackwell_sm120` shipped deliberately cautious defaults for then-new silicon:
experimental **cu132** torch wheels ("newest kernels, VERIFY before production"),
`compile_image=False`, `inference_speed_profile="fast_review"`,
`sam3_batched_text_chunk_size=8`. On a real RTX 5070 Ti these were all
out-performed, so the operator hand-tuned the generated `.env` block to cu130 +
`SAM3_COMPILE_IMAGE=1` + `recall_review` + chunk 64. But `configure_host.py`
wholesale-replaces the generated block, so **re-running it would silently revert
all of that tuning** (an 11-key clobber, incl. the torch/CUDA channel). That made
the documented "operators can override in `.env` after generation" workflow unsafe.

## What changed (in the profile, so regen is correct everywhere)

- **torch channel cu132 → cu130** (`torch_version 2.10.0+cu130`, torchvision
  `0.25.0`, torchaudio `2.10.0`). cu130 is the proven, stable channel already used
  by every other CUDA-13.x profile; the `12.0+PTX` arch entry JIT-compiles sm_120
  kernels and runs measurably well. cu132 was an experimental "kernel currency"
  bet; cu130 is the measured choice and unifies the wheel set across the matrix.
- **`compile_image=True`** — ~5.6×/chip on the RTX 5070 Ti, zero quality change
  (see [sam3-compile-and-chip-padding-2026-06-14.md](sam3-compile-and-chip-padding-2026-06-14.md)).
  Pairs with the worker's `INFERENCE_PAD_CHIPS_TO_SIZE=1`.
- **`inference_speed_profile="recall_review"`** — full raster coverage. The chip
  sweep is serialized, so coverage costs latency, not VRAM.
- **`sam3_batched_text_chunk_size=64`** — measured 8→17.6 / 32→13.3 ms/prompt;
  64 keeps most of the win with VRAM headroom.
- **`install_fast_deps` kept `False`** — FA3 doesn't run on sm_120 (Hopper-only),
  so building it only bloats the image; the runtime uses the torch SDPA fallback.

## Effect

A `configure_host.py --dry-run` against the tuned host now diverges on only two
keys, both benign: `SAM3_VISIBLE_DEVICES` (legitimate host detection — the
generator assigns the GPU id) and `SAM3_INSTALL_FAST_DEPS` (`.env=1` is a harmless
local build choice; the profile default `0` is leaner). The perf-critical tuning
(compile / recall_review / chunk / cu130) is now reproduced by the generator, so
regen no longer clobbers it. This is the "universal" fix: the profile is the source
of truth and its output is correct on any RTX 50-series host.

## Scope & risk

The profile covers **all** consumer Blackwell (RTX 5060/5070/5080/5090, 8–32 GiB):
- `compile_image` and `recall_review` are safe across the range (same arch;
  coverage is latency-bound, not VRAM-bound under the serialized forward path).
- **`chunk=64` is the one VRAM-sensitive bake.** On 8 GiB cards (RTX 5060) under
  the dynamic loading policy, decode at chunk 64 may OOM — those hosts should set
  `SAM3_BATCHED_TEXT_CHUNK_SIZE=8` in `.env`. Documented in the profile comment.
- cu130 reverts the deliberate cu132 "newest kernels" choice; acceptable because
  cu130 + 12.0+PTX is measured-good and the rest of the fleet is already cu130.

## Rollback

Per-knob `.env` override (`SAM3_COMPILE_IMAGE=0`, `INFERENCE_SPEED_PROFILE=fast_review`,
`SAM3_BATCHED_TEXT_CHUNK_SIZE=8`), or revert the profile fields. To return to the
experimental cu132 stack, restore the four torch fields.

## Cross-references

- [sam3-compile-and-chip-padding-2026-06-14.md](sam3-compile-and-chip-padding-2026-06-14.md) — the compile measurement this bakes in
- [dense-scene-recall-defaults.md](dense-scene-recall-defaults.md) — the NMS/recall defaults emitted alongside
- [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md) — the profile table + build args
- [why-dynamic-modality-loading-on-tight-vram.md](why-dynamic-modality-loading-on-tight-vram.md) — why 16 GiB cards run dynamic loading
- [why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md) — the version-independent cu13x poison
