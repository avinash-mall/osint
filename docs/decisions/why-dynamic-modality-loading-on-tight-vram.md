# Why dynamic per-modality model loading on tight-VRAM GPUs

**Date:** 2026-05-31
**Status:** adopted
**Source:** [scripts/gpu_profiles.py](../../scripts/gpu_profiles.py), [inference-sam3/main.py](../../inference-sam3/main.py)

## Problem

On a 16 GiB consumer Blackwell (RTX 5070 Ti) the `blackwell_sm120` profile loaded the
**monolithic `imagery` profile** — SAM3 + DINOv3-SAT + TerraMind + DOTA-OBB +
the then-optional open-vocabulary/verifier layers — resident at once (~14.7 GiB). That left no headroom,
so SAM3's batched-text forward OOMed on **every** chunk (`CUDA out of memory, tried to allocate
648 MiB, ~500 MiB free`). `_run_text_prompts_cached_batched` swallowed each chunk's OOM and
returned an empty candidate list, the `/detect` endpoint returned HTTP 200 with `detections=0`,
and the worker finalized the upload as `ready` with zero detections. Net effect: **uploads
"succeeded" but produced no detections** — the image-upload pipeline looked broken. DOTA-OBB
also failed with `cuDNN CUDNN_STATUS_BAD_PARAM` (no workspace memory).

## Layer evaluation (what earns its VRAM)

Measured on `pass_id=1` (austin1.tif, RGB, all models resident — the last run before the OOM
regression) and the WBF fusion source list in [inference-sam3/fusion.py](../../inference-sam3/fusion.py):

| Layer | Detection boxes | VRAM | Latency | Verdict |
|---|---|---|---|---|
| sam3 | 1832 / 1848 (99.1%) | core | core | **core — always resident** |
| dota_obb | 16 / 1848 (0.9%) | +0.1 GiB | +50 ms | **keep** (cheap, real boxes) |
| dinov3_sat | 0 (re-ID embeddings only) | +1.5 GiB | +217 ms | keep (re-ID); not a detector |
| terramind | 0 on RGB (SAR) | +1.2 GiB | — | **modality-specific** → SAR profile |

SAM3 does most detection work. DINOv3-SAT is enrichment, not a detector. TerraMind is
SAR-specific and must stay out of the RGB working set. FAIR1M-OBB, RemoteCLIP, and Prithvi were
removed later; see the removal decisions linked from [agent-entry.md](../agent-entry.md).

## Decision

Preserve **all four modalities** (RGB, multispectral, SAR, FMV) while fitting 16 GiB, via a
loading-policy lever chosen by measured VRAM in `GpuBuildProfile.runtime_env(vram_mib=…)`:

1. **Loading policy (hot vs dynamic).** `vram_mib >= sam3_hot_load_min_vram_mib` (24 GiB) →
   *hot*: the profile's own preload behaviour, full `imagery` union resident. Below it →
   *dynamic*: `SAM3_RESTING_PROFILE=imagery_rgb`, one modality profile resident at a time.

Per-modality profiles in `inference-sam3/main.py` `PROFILE_COMPONENTS` (`imagery_rgb` /
`imagery_msi` / `imagery_sar`, all sharing `sam3_image` + `dinov3_sat`); `/detect` routes by
request modality via `_profile_for_modality`. `_ensure_profile` short-circuits when a resident
superset (`imagery` union or `all`) already satisfies the request, so **hot cards never pay
swap latency**. A single upload is one modality and its chips share it, so the profile loads
once per upload; with `INFERENCE_CHIP_CONCURRENCY=1` on tight cards there is no swap thrash.

Terramind is therefore re-enabled on `blackwell_sm120` (it was force-disabled only because the
monolithic profile couldn't afford it) — it now goes resident only for SAR ingest.

## Result (measured 2026-05-31, RTX 5070 Ti 16 GiB)

- Resting `imagery_rgb` profile: ~5 GiB resident, **~10.8 GiB free** (was ~200 MiB).
- RGB ingest of austin1.tif: SAM3 batched forward succeeds (~96 ms, peak 7.3 GiB), **148
  detections** stored, zero failed chips, no OOM.
- DOTA-OBB cuDNN error gone (specialists run in ~13 ms).
- `fmv` profile loads on demand (8.6 GiB used / 7.2 GiB free), no OOM — dynamic swap works.

## Robustness (defence in depth)

`_run_text_prompts_cached_batched` now counts failed chunks; if **every** chunk fails it raises
instead of returning empty, so `/detect` returns non-200. `process_satellite_imagery` fails the
upload (`status='failed'`) when **every** attempted chip errored, instead of finalizing `ready`
with zero detections. This stops a future VRAM misconfig from silently masking as "no objects."

## Cross-references

- [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md)
- [inference/service-overview.md](../inference/service-overview.md)
- [decisions/why-yoloe-fp32-and-bf16-cast.md](why-yoloe-fp32-and-bf16-cast.md) — sibling
  silent-zero-detection regression on Blackwell.
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md)
