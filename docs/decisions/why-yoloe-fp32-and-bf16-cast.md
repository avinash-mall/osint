# YOLOE pinned fp32, plus `.float()` cast before numpy()

**Path:** [inference-sam3/yoloe.py](../../inference-sam3/yoloe.py)
**Lines touched:** ~15
**Depends on:** `ultralytics.YOLOE`, `torch.Tensor.float()` (CPU+GPU)

## Decision

Two small interlocking changes:

1. `YOLOE_HALF = False` and `YOLOE_CHANNELS_LAST = False` are **hardcoded** — the `SAM3_YOLO_HALF` / `SAM3_YOLO_CHANNELS_LAST` env vars emitted by [scripts/gpu_profiles.py:208](../../scripts/gpu_profiles.py#L208) are ignored for YOLOE specifically. `YOLOE_FUSE` still reads from env (fuse is safe).
2. The bbox / score / mask extraction block in `yoloe.run` casts tensors to fp32 with `.float()` (and `.long()` for class ids) before `.cpu().numpy()`.

## Why

Two independent failure modes were silently masking *every* FMV detection on this stack (RTX 5070 Ti / CUDA 13.2 / torch 2.10):

1. **fp16 + YOLOE Lrpc head** — Calling `model.half()` on a YOLOE checkpoint converts the body but leaves the `Lrpc` vocab head and the `set_classes` projection in fp32. `model.get_text_pe()` returns fp32, then `set_classes` tries to mix it with the fp16 body and raises `RuntimeError: mat1 and mat2 must have the same dtype, but got Float and Half`. The except clause in `yoloe.run` swallows this and returns `[]`. Pinning `YOLOE_HALF = False` keeps the body fp32 so the head's fp32 inputs match.
2. **bf16 → numpy()** — Even with `YOLOE_HALF = False`, autocast (and possibly ultralytics' own internal precision juggling) returns `Tensor` outputs in bf16. `boxes.conf.cpu().numpy()` raises `TypeError: Got unsupported ScalarType BFloat16`. The original `except Exception: continue` swallowed this too — for *every* result, on every frame, for every clip — so `tracks_seen=0` was logged for every window of every FMV upload even when the model was returning 9-10 boxes per frame.

After both fixes, on the same `Day_Flight.mpg` MISB clip that produced zero detections for two weeks:

- `yolo26 + amg` (YOLOE-PF) → **6 869** detections (scene-level LVIS labels: `leader`, `wine cooler`, `moped`, etc. — that's YOLOE-PF's baked vocab, not a code bug).
- `yolo26 + pcs` with prompts `car,truck,person,aircraft,building` → **301** detections (`car: 161, person: 100, aircraft: 38, building: 2`).

## Considered alternatives

- **Cast text_pe only, leave body fp16.** Tried — predict() then trips the same dtype error one layer deeper in the SwiGLU `w12` Linear. The Lrpc head has multiple fp32 sub-modules; partial casts can't reach them all.
- **Make the env vars respected; expect operators to set SAM3_YOLO_HALF=0.** Rejected — the gpu_profiles defaults force fp16 on all non-Turing profiles, and silently-zero detections are too pernicious a failure to leave on a "did you check the env?" tripwire.
- **Implement the documented `DISABLE_ADDMM_CUDA_LT` torch.addmm monkey-patch.** Rejected as the fix here — investigation showed it isn't the root cause; `torch.backends.cuda.preferred_blas_library()` already returns `_BlasBackend.Cublas` (non-Lt) by default in torch 2.10. The doc still references a patch that doesn't exist in code; that gap is a separate cleanup.
- **Cast at call sites in `sam3_runner.run_video_yoloe`.** Rejected — the bf16 leak is internal to `yoloe.run`'s return contract; consumers shouldn't need to know.

## How this surfaced

Commit `279cbe1473 v-0.11` (2026-05-19) added `apply_yolo_optimizations(half=YOLOE_HALF, …)` to YOLOE loading and `model.predict(half=YOLOE_HALF)` to the inference call. From that point onward every YOLOE-driven FMV upload returned zero detections on Blackwell + cu130. The fp16 dtype error went straight into the broad `except Exception: continue` and was never logged, so the only externally-visible symptom was an empty `fmv_detections` table.

## Trade-offs accepted

- Small perf give-up vs. fp16 on supported GPUs. fp16 may be safely re-enabled in the future if/when (a) ultralytics fixes the Lrpc dtype handling, and (b) the `.float()` casts here are kept as a safety net.
- One more place to remember when adding new tensor → numpy conversions in YOLOE-adjacent code.

## Cross-references

- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md)
- [decisions/why-yoloe-replaced-amg.md](why-yoloe-replaced-amg.md)
- [decisions/disable-addmm-cuda-lt.md](disable-addmm-cuda-lt.md) — references a torch.addmm patch that isn't in code; orthogonal to this fix
