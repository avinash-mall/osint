# Inference service audit — fixes (2026-06-12)

**Date:** 2026-06-12
**Status:** adopted

## Context

A correctness audit of the GPU inference service (`inference-sam3/`) surfaced
ten verified defects. Most are paths that escaped three intentional designs —
process-wide forward serialization
([why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md)),
the poisoned-CUDA-context `os._exit(1)` self-heal
([why-exit-on-poisoned-cuda-context.md](why-exit-on-poisoned-cuda-context.md)),
and the 503-on-unloaded-component guard
([why-503-on-unloaded-component.md](why-503-on-unloaded-component.md)) — so the
fixes *extend* those designs rather than invent new mechanisms. Fixes are
confined to [main.py](../../inference-sam3/main.py),
[sam3_runner.py](../../inference-sam3/sam3_runner.py),
[yoloe.py](../../inference-sam3/yoloe.py),
[fusion.py](../../inference-sam3/fusion.py),
[sar.py](../../inference-sam3/sar.py),
[terramind.py](../../inference-sam3/terramind.py); CPU-testable fixes are
regression-tested in
[tests/test_audit_fixes.py](../../inference-sam3/tests/test_audit_fixes.py).

## Fixed

**SAR per-detection TerraMind re-forward** ([main.py](../../inference-sam3/main.py) `_detect_pipeline`)
- `terramind.pool_patches` pools patch tokens over the *whole* chip, so its
  result is identical for every detection — yet it re-ran per detection,
  synchronously on the event loop, outside any forward lock, without a device
  pin. It now runs **once** per SAR chip, via `run_in_threadpool` under the
  pipeline's `_locked` forward-lock wrapper, and the same result dict is
  stamped onto every SAR detection. `pool_patches` (and `s1_to_s2_rgb`) pin the
  current CUDA device via `inference_utils.device_ctx`, like the other
  specialists.

**SAR S1→S2 decode escaped the lock and the self-heal** ([main.py](../../inference-sam3/main.py) `/detect`)
- `terramind.s1_to_s2_rgb` is a real GPU forward but runs *before*
  `_detect_pipeline_guarded`, so it raced other forwards on serialize-forwards
  hosts, and its `except → HTTPException 400` masked a poisoned CUDA context as
  a client error. The decode now runs under `bundle["forward_lock"]`, and the
  except handler classifies via `sam3_runner._cuda_context_poisoned` → logs
  critical + `os._exit(1)` before anything is mapped to a 400.

**Auto-heal profile swap under a live request** ([main.py](../../inference-sam3/main.py) `_ensure_profile`)
- The auto-heal swap tore down the resident pool in place with no in-flight
  check, so an imagery `/detect` or `/embed` arriving mid-FMV nulled
  `sam3_video`/`yoloe` under the running stream. `_ensure_profile` now refuses
  a real teardown+reload with **503** while any *other* request is in flight
  (`_active_requests > 1` — the caller has already counted itself); the worker
  treats 503 as retryable backpressure. Mirrors `/load`'s 409 guard.

**PCS `/detect_video` missing the 503 component guard** ([main.py](../../inference-sam3/main.py) `detect_video`)
- The YOLOE branch guarded a missing component; the PCS branch dereferenced
  `reserved["sam3_video"]` inside the already-started NDJSON generator and died
  with an AttributeError mid-stream. It now raises the same honest 503 before
  the stream starts ([why-503-on-unloaded-component.md](why-503-on-unloaded-component.md)).

**Poisoned context swallowed in video paths** ([sam3_runner.py](../../inference-sam3/sam3_runner.py) `run_video_yoloe`, [yoloe.py](../../inference-sam3/yoloe.py) `run`, [main.py](../../inference-sam3/main.py) both `stream()` generators)
- `run_video_yoloe`'s per-frame except swallowed unrecoverable CUDA faults into
  `candidates = []` and `yoloe.run`'s outer except returned `[]` — a
  healthy-looking zombie emitting zero detections for every remaining frame;
  the PCS path propagated the exception but never self-healed. All four sites
  now classify via `_cuda_context_poisoned`: `yoloe.run` re-raises poisoned
  faults (instead of returning `[]`), `run_video_yoloe` and both `/detect_video`
  stream generators log critical + `os._exit(1)`.

**Production never loaded the backend detection policy** ([fusion.py](../../inference-sam3/fusion.py))
- The single-path loader used `parents[1]/"backend"/detection_policy.py`, which
  is `/backend` inside the container — never exists — so production always fell
  back to the naive slugifier. The loader now tries candidates in order:
  `/app/detection_policy.py` (the compose file-mount) first, then the dev-host
  `../backend/` checkout, accepting a candidate only if it is non-empty **and**
  defines `parent_class_for_label` (rejects the 0-byte host-side bind-mount
  anchor). See [removed-empty-inference-detection-policy.md](removed-empty-inference-detection-policy.md)
  (corrected in the same pass).

**`/embed` ran unlocked and uncounted** ([main.py](../../inference-sam3/main.py) `embed_endpoint`)
- The DINOv3 forward took no forward lock (racing other forwards on
  serialize-forwards hosts) and never `_enter_request`/`_leave_request`ed, so
  the `/load`//`/unload` in-flight guards — and the new swap guard — were blind
  to running embeds. Now bracketed with request accounting, run under
  `bundle["forward_lock"]`, with exceptions routed through the
  `_cuda_context_poisoned` → `os._exit(1)` check.

**YOLOE pf→seg fallback always emitted nothing** ([yoloe.py](../../inference-sam3/yoloe.py) `run`)
- When the `-pf` checkpoint was missing the code fell back to the `-seg` model
  but still called `model.set_classes([], model.get_text_pe([]))` with the
  empty prompt list — which raises (or leaves a zero-class vocab). With no
  prompts the fallback now runs seg with its baked vocabulary and logs a clear
  warning; `set_classes` is only called for a non-empty prompt list.

**NaN nodata produced garbage SAR chips with HTTP 200** ([sar.py](../../inference-sam3/sar.py) `decode_s1grd`, [terramind.py](../../inference-sam3/terramind.py) `s1_to_s2_rgb`)
- S1 GRD swath-edge NaNs passed through clip/normalize, smeared via
  `cv2.resize`, and turned the percentile stretch into an all-black chip —
  zero detections reported as success. `decode_s1grd` now `np.nan_to_num`s
  after read (NaN → 0 in the linear-power branch, which the dB conversion
  floors; NaN → `SAR_DB_FLOOR` in the dB branch), mirroring the MSI path;
  `s1_to_s2_rgb` adds a belt-and-braces `nan_to_num` before its percentile
  stretch.

**Prompt cap dead on the production branch** ([main.py](../../inference-sam3/main.py) `resolve_prompts`)
- `_prompt_limit` (`metadata.max_prompts` bounded by
  `SAM3_MAX_PROMPTS_PER_REQUEST`/`SAM3_MAX_IMAGE_PROMPTS`/`SAM3_MAX_VIDEO_PROMPTS`)
  applied only to the precision-default and ontology-fallback branches; the
  explicit `text_prompts` branch — the path the worker uses in production —
  was uncapped. The explicit branch now applies the same cap and logs on
  truncation.

**Docs drift** — `metadata.hls_timesteps` removed from
[inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md) and the
nonexistent `crop:*` overlay labels from
[inference/fusion-and-nms.md](../inference/fusion-and-nms.md) (Prithvi loads
only the flood/burn heads).

## Why this design

- Every GPU forward in the process must sit under the forward lock and inside
  a `_cuda_context_poisoned` → `os._exit(1)` boundary — the audit found the
  escapes (pre-guarded SAR decode, `/embed`, video streams) and closed them
  with the *same* mechanisms rather than per-path variants.
- Returning `[]` / emitting an empty stream on an unrecoverable fault is the
  worst failure mode for an air-gapped analyst platform: it reports success
  while detecting nothing. Re-raising + process exit converts it into a visible
  ~100 s restart that the worker already rides out
  ([why-retry-chips-across-inference-restart.md](why-retry-chips-across-inference-restart.md)).
- 503 (retryable backpressure) is the established contract for "momentarily
  not holding what you need" — the swap guard and the PCS guard reuse it
  instead of letting requests crash mid-flight.

## Cross-references

- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md)
- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md)
- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md)
- [why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md)
- [why-exit-on-poisoned-cuda-context.md](why-exit-on-poisoned-cuda-context.md)
- [why-503-on-unloaded-component.md](why-503-on-unloaded-component.md)
- [removed-empty-inference-detection-policy.md](removed-empty-inference-detection-policy.md)
