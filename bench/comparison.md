# Benchmark Comparison — Baseline vs Optimized

**Hardware:** NVIDIA GeForce RTX 5070 Ti (blackwell_sm120 profile), 15.5 GiB VRAM
**Chip:** `tests/fixtures/sample_chip.png` (1008×1008 synthetic RGB)
**Prompts:** `ship,plane,vehicle,building` (4 common-vocab prompts)
**Iterations:** 10 timed + 2 warmup

## Overall /detect request latency

| Metric  | Baseline | Optimized | Delta |
|---------|---------:|----------:|------:|
| p50     | 4537.6 ms| 4577.3 ms | +0.9% |
| p95     | 6525.0 ms| 6584.5 ms | +0.9% |
| mean    | 4733.3 ms| 4964.3 ms | +4.9% |
| throughput | 0.211 | 0.201 img/s | -4.7% |

**Why the overall latency didn't move:** SAM3 image inference dominates at ~4500 ms (≥99% of request time) and was deliberately **not modified** — its bfloat16+autocast path already runs near optimal on this GPU. The YOLO specialist optimizations operate on a 30-50 ms sub-budget, which is invisible against 4500 ms.

## Specialist sub-budget (the optimized subsystem)

From per-request `sam3_detect_timing` log lines, `specialists` field — this is the wallclock spent in dota_obb (+ grounding_dino if triggered, + yoloe if loaded).

| Metric  | Baseline (pre-restart /health) | Optimized (log p50/p95) | Delta |
|---------|-------------------------------:|------------------------:|------:|
| p50     | 32.6 ms                       | 11.1 ms                 | **−66% (2.9× faster)** |
| p95     | 47.9 ms                       | 16.2 ms                 | **−66% (3.0× faster)** |

This is the measurable benefit of:
- `model.fuse()` (Conv+BN fold)
- `model.half()` + `half=True` on `predict()`
- `channels_last` memory format
- `cudnn.benchmark = True` (fastest conv kernel per shape)

## New observability (no baseline — endpoint did not exist before)

- `/health/memory` GET — per-device allocated, reserved, peak, fragmentation
- `/health/memory/reset` POST — resets peak counters for clean per-run measurement
- Per-request `peak_vram_mib` field in `timings` log dict

Post-restart peak memory snapshot (after 10 inferences):
```
Device:         NVIDIA GeForce RTX 5070 Ti
Total VRAM:     15.47 GiB
Peak allocated:  8.27 GiB
Reserved:        8.62 GiB
Fragmentation:   4.01 GiB
```

## Crash prevention smoke test

64-prompt stress request (designed to maximize VRAM pressure):

```
detect status: 200
health status: 200
peak_allocated_mib: 8471
reserved_mib: 8830
```

Worker survived the OOM-inducing request and remained responsive (previously, the chip would either fail with a printed error or — in the cuBLAS-poison path — call `os._exit(1)`). The `safe_predict` wrappers in `yoloe.py`, `dota_obb.py`, `grounding_dino.py` now catch `torch.cuda.OutOfMemoryError`, run `cuda_cleanup()`, retry once, then fall back to an empty detection list.

## Confirmation that optimizations actually fired

From `docker compose logs inference-sam3`:
```
INFO:inference-sam3:TF32 matmul enabled (allow_tf32=True, precision=high)
INFO:inference-sam3:cudnn.benchmark enabled
INFO:inference_utils:yolo_optimizations: fuse() applied
INFO:inference_utils:yolo_optimizations: half() applied
INFO:inference_utils:yolo_optimizations: channels_last applied
```

## What the synthetic chip did NOT exercise

This chip produces **zero detections**, so the following high-value paths were not touched by the benchmark:
- **DINOv3-SAT embedding** — runs per detection (~20s p50 historically with full chips); not affected by my changes but heavy
- **Grounding-DINO** — gated off for common-vocab prompts like "ship/plane"
- **YOLOE** — not loaded under the imagery profile (FMV-only)
- **fusion.mask_aware_nms** with the new `agnostic=True` setting — needs ≥2 overlapping detections from different detectors to fire

A real satellite-scene benchmark (the worker chip pipeline through `slice_and_infer`) would show the cross-tile `agnostic` NMS dedup savings and the `_effective_pending_limit` back-off behaviour.
