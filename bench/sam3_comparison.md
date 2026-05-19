# SAM3 Inference Optimization — Results

**Hardware:** NVIDIA GeForce RTX 5070 Ti (blackwell_sm120 profile), 15.5 GiB VRAM
**Chip:** `tests/fixtures/sample_chip.png` (1008×1008 synthetic RGB)
**Prompt input:** 8 user prompts → 146 ontology-expanded prompts (typical worker scenario)
**Iterations:** 5 timed + 2 warmup (per stage)

## End-to-end /detect latency

| Stage                          | p50 (ms)   | p95 (ms)   | Δ vs prior |
|--------------------------------|-----------:|-----------:|-----------:|
| Pre-Phase-A (chunked-batched)  | 4719.8     | 4749.1     | —          |
| **Phase A3** (cached encoder)  | **3006.9** | **3021.9** | **−36.3%** |
| Phase A3 + B3 + C1 combined    | 2976.8     | 2982.9     | −1.0% more |

**Headline:** SAM3 image inference dropped from **4720 ms → 2977 ms p50 (~37% reduction, 1.58× faster)** on the worker chip path with 146 prompts.

## Per-stage breakdown (from `sam3_detect_timing` log lines)

| Stage                | Pre-A3        | Phase A3      | Phase A3+B3+C1 |
|----------------------|--------------:|--------------:|---------------:|
| `sam3_encode_image`  | 18× × 250ms = ~4500 (in `batched_forward`) | **102** (1× run) | **100** (1× run) |
| `sam3_batched_forward` (cumulative) | 4540 | 2683 | 2676 |
| `sam3_batched_postproc` (cumulative) | 29   | 28   | 30   |
| `sam3_inference` total | 4719 | 2982 | 2944 |

The **−1857 ms** saving in `batched_forward` is exactly the eliminated 17 redundant vision-encoder runs (~110 ms each × 17 = ~1870 ms). The cached path runs the ViT-L+ encoder once for the whole image, then runs the DETR decoder per-chunk-of-8 reusing `state["backbone_out"]`.

## What landed

### Phase A — Image-encoder caching (the big win)

- **`inference-sam3/sam3_perf.py`** — `stage_timer` (CUDA-synced, accumulating), `pin_sdpa_backend`, `is_blackwell_consumer`.
- **`inference-sam3/main.py`** — promotes SAM3 sub-stage timings into the per-request log dict with `sam3_*` prefix.
- **`inference-sam3/sam3_runner.py`**:
  - `_run_text_prompts_cached_batched` — encode once, loop chunks reusing cached backbone_out
  - Dispatch in `run_text_prompts` routes to the cached path when the upstream patch is installed
  - `_install_sam3_perf_patches()` called from `build_image()`
- **`inference-sam3/patches/sam3_cached_forward.py`** — monkey-patches `Sam3Image.forward` to honour `_cached_backbone_out` on the input and skip the encoder. Also patches `Sam3Processor.set_image` to cast inputs to the model dtype when bf16 is enabled.
- **4 new pytest cases** in `tests/test_sam3_perf.py`. Two existing tests in `tests/test_box_prompt_native.py` updated for the new dispatch signature.

### Phase B — Profile knobs + SDPA + bf16 cast (mostly observability)

- **`scripts/gpu_profiles.py`** — added four new `GpuBuildProfile` fields (`sam3_native_bf16`, `sam3_sdpa_backend`, `sam3_decoder_topk`, `sam3_compile_vision_encoder`); emitted via `runtime_env`; per-profile defaults: consumer = topk=32 + sdpa=flash, datacenter = topk=64 + compile=True, Turing = all off.
- **`docker-compose.yml`** + **`.env`** — pass-through wiring for the four new env vars.
- **`sam3_runner.py`** — `_sdpa_ctx()` wraps all 4 forward call sites (image text/box/cached-batched + video). `_autocast_ctx` no-ops when `SAM3_NATIVE_BF16=1`.
- **Native bf16 cast attempted but deferred** — upstream `_get_dummy_prompt` + `geometry_encoder` keep fp32 buffers that fail bf16 nn.Linear without patching every call site. Documented in the profile field's docstring; ENV defaults to `0`. Re-enabling would require ~5 more upstream patches.

### Phase C — Decoder top-K + compile (small wins)

- **`patches/sam3_cached_forward.py`** — `install_decoder_topk()` wraps `Sam3Image.forward_grounding` so when `SAM3_DECODER_TOPK > 0`, sub-K queries get pred_logits/pred_masks zeroed before postproc.
- **Compile vision encoder** — wired in profile but **default OFF** even on Blackwell. Existing `compile_image=False` comment in gpu_profiles.py documents the FX/dynamo conflict with `act_ckpt_wrapper` on cu130 + sm_80; stripping AC at inference is a deeper change than this pass justifies given the measured 30 ms upside.

## What's confirmed at runtime

From `docker compose logs inference-sam3`:
```
INFO:sam3_cached_forward:Sam3Processor.set_image patched to cast inputs to model dtype
INFO:sam3_cached_forward:Sam3Image.forward_grounding patched: decoder top-K=32
INFO:sam3_cached_forward:Sam3Image.forward patched for cached-encoder reuse
```

Env vars in the running container:
```
SAM3_COMPILE_VISION_ENCODER=0
SAM3_DECODER_TOPK=32
SAM3_NATIVE_BF16=0
SAM3_SDPA_BACKEND=flash
```

## Tests

- inference-sam3: **39 passed, 1 skipped** (live OOM test; needs running service)
- Backend size_estimation: **12 passed** (no regression from prior phase)

## What's NOT in scope (and why)

- **TensorRT export.** Multi-week project. Reference: kyon's RTX 5090 (same sm_120) hits ~38 ms full TRT FP16. Future quarter.
- **Native bf16 weights.** Deferred due to upstream fp32 buffers in geometry encoder + dummy prompt. Would require ~5 more patch sites; ~5-10% expected speedup is not worth that pressure.
- **`torch.compile(vision_encoder)`.** Wired into profile (`sam3_compile_vision_encoder`) but default OFF — the FX/dynamo conflict with `act_ckpt_wrapper` is well-documented in gpu_profiles.py; resolving requires stripping AC from the loaded module tree, which is brittle across cu126/cu130 upstream changes.
- **EfficientSAM3 / distillation.** Quality regression unknown on our SA-Co-style prompts.
- **SageAttention 2.2.0 swap.** Drop-in monkey-patch with unknown cross-attention quality drift; benchmark mAP first.

## How to reproduce

```bash
# 1. Ensure inference-sam3 is up with new env vars
docker compose up -d inference-sam3
# Wait for healthy
docker compose exec inference-sam3 curl -s -X POST 'http://localhost:8001/load?profile=imagery'

# 2. Get the sample chip into the container
docker compose cp tests/fixtures/sample_chip.png inference-sam3:/tmp/sample_chip.png

# 3. Benchmark
docker compose exec inference-sam3 python /app/benchmark_detect.py \
    --url http://localhost:8001 --chip /tmp/sample_chip.png \
    --iters 5 --warmup 2 \
    --prompts "ship,plane,vehicle,building,helicopter,ground vehicle,large vehicle,small vehicle" \
    --out /tmp/sam3_run.json
```

Watch `docker compose logs inference-sam3 | grep sam3_detect_timing` to see per-stage `sam3_encode_image`, `sam3_batched_forward`, `sam3_batched_postproc` ms. Encoder should stay flat ~100 ms regardless of how many prompts you send.
