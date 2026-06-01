# Decision: normalise the find-side input onto self.device in the cached SAM3 forward

## Context

`patches/sam3_cached_forward.py` monkey-patches `Sam3Image.forward` with `forward_with_cache` — skips the vision encoder when cached image features are stashed on the input (`_cached_backbone_out`). It mirrors the original forward but replaces the image-encoder call with the stashed features.

The mirror was incomplete. Upstream `Sam3Image.forward` normalises the whole datapoint onto `self.device` before calling `forward_grounding`. The patched forward bypassed that — consumed `input.find_inputs[0]` as-is.

`/detect_raw` then failed in the cached batched path:

```
File ".../sam3/model/sam3_image.py", line 131, in _get_img_feats
    x[img_ids].flatten(2).permute(2, 0, 1) for x in vis_pos_enc
RuntimeError: indices should be either on cpu or on the same device as the indexed tensor (cuda:0)
```

`vis_pos_enc` (from cached `backbone_out`) sat on the replica's device while `find_input.img_ids` sat on a different CUDA device. Only bites on multi-GPU hosts: PyTorch's current CUDA device is thread-local, the inference service runs `/detect` under an anyio threadpool whose threads are reused across replicas on different GPUs → the device a tensor is created on can drift from the replica consuming it. Single-GPU hosts never diverge → why the cached path passed earlier testing.

> **Superseded in part — see [Follow-up](#follow-up-the-mover-was-a-silent-no-op) below.**
> The `_move_tensors_to_device` approach recorded here did *not* fix the crash: it is a silent no-op on SAM3's frozen-dataclass / `__slots__` find-side objects. The real fix pins the CUDA device upstream of the collator. This section kept for history.

## Decision

In `forward_with_cache`, before reading `find_input`/`find_target`, move every find-side tensor onto the device the cached vision features actually live on:

```python
feat_device = _first_tensor_device(backbone_out)   # device of the cached vis_pos_enc
if feat_device is not None:
    _move_tensors_to_device(input.find_inputs, feat_device)
    _move_tensors_to_device(input.find_targets, feat_device)
```

`_get_img_feats` indexes `backbone_out["vis_pos_enc"]` with `find_input.img_ids` → the index tensor must be co-located with the *cached* features. Device derived from `backbone_out` itself, not `self.device` — `backbone_out` is the literal operand being indexed, the unambiguous source of truth.

First attempt used upstream `copy_data_to_device`, which **did not fix the crash**: it recurses known container types (tensor/list/dict/tuple) but treats SAM3's `FindInput`/`FindTarget` as opaque, returns them untouched → `img_ids` never moved. `_move_tensors_to_device` ([sam3_cached_forward.py](../../inference-sam3/patches/sam3_cached_forward.py)) is a local recursive mover that also descends into plain objects via `__dict__`, bounded depth → `img_ids`, `input_boxes`, masks, labels all moved.

## Alternatives considered

- **`copy_data_to_device` (first attempt)** — doesn't recurse into `FindInput`; left `img_ids` on the wrong device, crash unchanged.
- **Move the cached `backbone_out` to `img_ids`'s device** — wrong direction: backbone features are large, `img_ids` tiny, and `img_ids` may be on a device disagreeing with model weights / decoder.
- **Move only `find_input.img_ids`** — fixes the observed crash but leaves other find-side tensors (`input_boxes`, used to build `geometric_prompt`) liable to the same mismatch if interactive steps run in eval mode.
- **Key the move off `self.device`** — works only if `self.device` agrees with the cached features' device; deriving from `backbone_out` is strictly safer.
- **Pin `bundle["device"]` harder upstream** — doesn't help: the divergence is the thread-local current device, not the bundle config.

## Scope

Only the cached fast path was missing the normalisation. The non-cached `_run_text_prompts_batched` calls `model(batch)` straight after `copy_data_to_device(batch, ...)`, routes through the unpatched upstream forward which normalises on its own — safe by construction.

## Follow-up: the mover was a silent no-op

The `_move_tensors_to_device` fix above shipped but the crash persisted on the multi-GPU host. Cause: SAM3's `FindInput`/`FindTarget` are **frozen dataclasses / `__slots__` objects**. The mover's plain-object branch either hit a swallowed `FrozenInstanceError` (a subclass of `AttributeError`, caught and ignored) or was skipped entirely because `__dict__` was `None` on a slotted object. `find_input.img_ids` was never moved — single-GPU testing never diverged → the regression was invisible.

The real fix is two layers:

- **Layer 1 — pin the CUDA device (root cause).** A `_device_ctx(device)` context wraps the inference `with` stacks in `sam3_runner.py`. PyTorch's current CUDA device is thread-local; under the anyio threadpool it drifts across replicas. The SAM3 collator (`collate_fn_api`) builds `img_ids` on the *current* device — pinning it to the replica's device makes the collator place `img_ids` on the right GPU in the first place → no later move needed. Distinct from the rejected "pin `bundle["device"]`" alternative: that pinned a config field; this pins the actual thread-local `torch.cuda` current device, which *is* the divergence.
- **Layer 2 — hardened mover (defense in depth).** `_move_tensors_to_device` now enumerates attribute names from both `__dict__` and the `__slots__` of every class in the MRO, falls back to `object.__setattr__` (documented escape hatch for frozen dataclasses) then direct `__dict__` mutation when `setattr` is blocked. The `forward_with_cache` normalisation is therefore now a genuine backstop, not a no-op.

A per-chunk `try/except` was also added to `_run_text_prompts_cached_batched`: a failing chunk is logged and skipped → the tile returns partial detections instead of 500-ing the whole `/detect_raw` request.

## Follow-up 2: the hardened mover still missed `img_ids` on a real 4-GPU host

Layer 2 above (the hardened in-place `_move_tensors_to_device`) was still a no-op against a live 4× A100 deployment — `/detect_raw` kept dying with the same `indices should be ... same device` error, with the cached `vis_pos_enc` on `cuda:0` and `find_input.img_ids` on another CUDA device. Layer 1's `_device_ctx` pin was confirmed active around both the collate and the forward, yet `img_ids` still diverged, so neither layer was actually co-locating it.

Reading the upstream source (`sam3` @ `ea46ebca`) settled it:

- `find_input` is a **`FindStage`** (`sam3.model.data_misc`), not a "FindInput". `img_ids` is collated as a Python list and converted to a tensor by `convert_my_tensors()` with **no `device=`**.
- **`copy_data_to_device` is dataclass-aware**: it recurses dataclasses and `.to()`-capable objects and **returns a moved copy**. But the code only ever called it on the **whole batch** (`BatchedDatapoint`), whose top-level type is not a dataclass → recursion hit the `return data` fallback and never descended into the stages. So `img_ids` was never moved by it. The Alternatives note above ("`copy_data_to_device` doesn't recurse into `FindInput`") was therefore half-right for the wrong reason: it does recurse dataclasses, just not when handed the non-dataclass batch.
- The in-place `_move_tensors_to_device` mover attempted attribute mutation on the stage and could not reliably reach `img_ids` on the running version's stage type.

**The fix (Layer 3, the one that actually co-locates):** in `forward_with_cache`, call `copy_data_to_device` **directly on each `FindStage` / `FindTarget`** and use the returned (moved) object as `find_input` / `find_target` — using the helper the way it was designed (per-dataclass, return-a-copy) rather than batch-level. The old in-place mover is retained only as a backstop for stage types that are neither dataclass nor `_CopyableData`, and a one-shot diagnostic (`_log_device_normalise_once`) logs `img_ids`'s device before/after against the cached features' device to confirm the outcome on the box. `_device_ctx` (Layer 1) stays as the first line of defence.

> The Layer 2 claim above ("genuine backstop, not a no-op") was optimistic — it had not been exercised on a true multi-GPU host. Layer 3 co-locates `img_ids`, but see Follow-up 3: `img_ids` was never the operand at fault.

## Follow-up 3: the real cause was a split `backbone_out`, not the find side

Layer 3 shipped with a one-shot diagnostic, and the first run on the 4× A100 host finally pinned the mechanism:

```
cached-forward find-side device normalise: stage=FindStage img_ids cuda:1 -> cuda:1 (feat_device=cuda:1)
RuntimeError: indices ... same device as the indexed tensor (cuda:0)
```

`img_ids` was already on `cuda:1` — Layer 1's `_device_ctx` had been working all along, and **the find side was never the problem.** The *indexed* operand, `vis_pos_enc`, was on `cuda:0`. So the cached `backbone_out` was **split across GPUs**: most tensors on the replica's device, the vision positional encodings on `cuda:0`.

Cause: the image-model build (`_build_image_impl`) was **not** wrapped in `_device_context(device)`. `build_sam3_image_model(device=...).to(device)` moves parameters and registered buffers to the replica's GPU, but the model creates non-param tensors (the positional encodings) on the *current* CUDA device — `cuda:0` by default — at build time, and `.to(device)` does not relocate them. Every replica ran with weights on `cuda:N` but pos-enc stuck on `cuda:0`; `_get_img_feats` then indexed a `cuda:0` `vis_pos_enc` with `cuda:N` `img_ids`. That illegal index also poisons the CUDA context → the follow-on `cudaErrorIllegalAddress` cascade. `build_video` already guarded against exactly this; the image build did not.

**Fix (the one that resolves it):**
- Wrap the image-model build in `_device_context(device)` in `_build_image_impl`, so pos-enc and any other current-device tensors are created on the replica's GPU. Mirrors `build_video`.
- Belt-and-suspenders: in `_run_text_prompts_cached_batched`, run the freshly-built `cached_backbone_out` through `copy_data_to_device(..., torch.device(device))` so the indexed operand is guaranteed co-located with `img_ids` (a no-op once the build is correct).
- Layers 1–3 stay — they were correct, just aimed at an operand (`img_ids`) that was already fine. The diagnostic now also logs the device set of `backbone_out` to catch any future split.

Lesson: key device debugging off the **indexed operand** (`vis_pos_enc`), not the index (`img_ids`). Three iterations chased the index; the operand was the one on the wrong GPU, because `.to(device)` is not a guarantee that *every* tensor a module produces is on `device`.

## Cross-references

- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md)
- [inference/sam3-perf-profiling.md](../inference/sam3-perf-profiling.md)
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)
