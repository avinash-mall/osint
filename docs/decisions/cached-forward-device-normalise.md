# Decision: normalise the find-side input onto self.device in the cached SAM3 forward

## Context

`patches/sam3_cached_forward.py` monkey-patches `Sam3Image.forward` with
`forward_with_cache`, which skips the vision encoder when cached image features
are stashed on the input (`_cached_backbone_out`). It "mirrors the original
forward but replaces the image-encoder call with the stashed features".

The mirror was incomplete. The upstream `Sam3Image.forward` normalises the whole
datapoint onto `self.device` before calling `forward_grounding`. The patched
forward bypassed that step — it consumed `input.find_inputs[0]` as-is.

`/detect_raw` then failed in the cached batched path:

```
File ".../sam3/model/sam3_image.py", line 131, in _get_img_feats
    x[img_ids].flatten(2).permute(2, 0, 1) for x in vis_pos_enc
RuntimeError: indices should be either on cpu or on the same device as the indexed tensor (cuda:0)
```

`vis_pos_enc` (from the cached `backbone_out`) sat on the replica's device while
`find_input.img_ids` sat on a different CUDA device. This only bites on
multi-GPU hosts: PyTorch's current CUDA device is thread-local, and the inference
service runs `/detect` under an anyio threadpool whose threads are reused across
replicas on different GPUs, so the device a tensor is created on can drift from
the replica that ends up consuming it. Single-GPU hosts never diverge, which is
why the cached path passed earlier testing.

> **Superseded in part — see [Follow-up](#follow-up-the-mover-was-a-silent-no-op) below.**
> The `_move_tensors_to_device` approach recorded here did *not* actually fix
> the crash: it is a silent no-op on SAM3's frozen-dataclass / `__slots__`
> find-side objects. The real fix pins the CUDA device upstream of the
> collator. This section is kept for history.

## Decision

In `forward_with_cache`, before reading `find_input`/`find_target`, move every
find-side tensor onto the device the cached vision features actually live on:

```python
feat_device = _first_tensor_device(backbone_out)   # device of the cached vis_pos_enc
if feat_device is not None:
    _move_tensors_to_device(input.find_inputs, feat_device)
    _move_tensors_to_device(input.find_targets, feat_device)
```

`_get_img_feats` indexes `backbone_out["vis_pos_enc"]` with `find_input.img_ids`,
so the index tensor must be co-located with the *cached* features. The device is
derived from `backbone_out` itself rather than `self.device` — `backbone_out` is
the literal operand being indexed, so it is the unambiguous source of truth.

The first attempt used the upstream `copy_data_to_device` helper, which **did
not fix the crash**: it recurses known container types (tensor/list/dict/tuple)
but treats SAM3's `FindInput` / `FindTarget` objects as opaque and returns them
untouched, so `img_ids` never moved. `_move_tensors_to_device`
([sam3_cached_forward.py](../../inference-sam3/patches/sam3_cached_forward.py))
is a local recursive mover that also descends into plain objects via
`__dict__`, with a bounded depth, so `img_ids`, `input_boxes`, masks and labels
are all moved.

## Alternatives considered

- **`copy_data_to_device` (first attempt)** — does not recurse into `FindInput`;
  left `img_ids` on the wrong device, crash unchanged.
- **Move the cached `backbone_out` to `img_ids`'s device** — wrong direction:
  the backbone features are large, `img_ids` is tiny, and `img_ids` may be on a
  device that disagrees with the model weights / decoder.
- **Move only `find_input.img_ids`** — fixes the observed crash but leaves other
  find-side tensors (`input_boxes`, used to build `geometric_prompt`) liable to
  the same mismatch if interactive steps run in eval mode.
- **Key the move off `self.device`** — works only if `self.device` agrees with
  the cached features' device; deriving from `backbone_out` is strictly safer.
- **Pin `bundle["device"]` harder upstream** — does not help: the divergence is
  the thread-local current device, not the bundle config.

## Scope

Only the cached fast path was missing the normalisation. The non-cached
`_run_text_prompts_batched` calls `model(batch)` straight after
`copy_data_to_device(batch, ...)` and routes through the unpatched upstream
forward, which normalises on its own — it is safe by construction.

## Follow-up: the mover was a silent no-op

The `_move_tensors_to_device` fix above shipped but the crash persisted on the
multi-GPU host. Cause: SAM3's `FindInput` / `FindTarget` are **frozen
dataclasses / `__slots__` objects**. The mover's plain-object branch either hit
a swallowed `FrozenInstanceError` (a subclass of `AttributeError`, caught and
ignored) or was skipped entirely because `__dict__` was `None` on a slotted
object. `find_input.img_ids` was never moved — single-GPU testing never
diverged so the regression was invisible.

The real fix is two layers:

- **Layer 1 — pin the CUDA device (root cause).** A `_device_ctx(device)`
  context wraps the inference `with` stacks in `sam3_runner.py`. PyTorch's
  current CUDA device is thread-local; under the anyio threadpool it drifts
  across replicas. The SAM3 collator (`collate_fn_api`) builds `img_ids` on the
  *current* device — pinning it to the replica's device makes the collator
  place `img_ids` on the right GPU in the first place, so no later move is
  needed. This is distinct from the rejected "pin `bundle["device"]`"
  alternative below: that pinned a config field; this pins the actual
  thread-local `torch.cuda` current device, which *is* the divergence.
- **Layer 2 — hardened mover (defense in depth).** `_move_tensors_to_device`
  now enumerates attribute names from both `__dict__` and the `__slots__` of
  every class in the MRO, and falls back to `object.__setattr__` (the
  documented escape hatch for frozen dataclasses) then direct `__dict__`
  mutation when `setattr` is blocked. The `forward_with_cache` normalisation is
  therefore now a genuine backstop, not a no-op.

A per-chunk `try/except` was also added to `_run_text_prompts_cached_batched`:
a failing chunk is logged and skipped so the tile returns partial detections
instead of 500-ing the whole `/detect_raw` request.

## Cross-references

- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md)
- [inference/sam3-perf-profiling.md](../inference/sam3-perf-profiling.md)
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)
