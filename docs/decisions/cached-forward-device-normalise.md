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

## Decision

In `forward_with_cache`, before reading `find_input`/`find_target`, re-apply the
upstream normalisation:

```python
_dev = device if isinstance(device, torch.device) else torch.device(device)
input.find_inputs  = copy_data_to_device(input.find_inputs,  _dev, non_blocking=_dev.type == "cuda")
input.find_targets = copy_data_to_device(input.find_targets, _dev, non_blocking=_dev.type == "cuda")
```

`device` is `self.device` — the replica's own device, which the cached
`backbone_out` was produced on (the encode `model.backbone.forward_image(...)`
succeeds, proving the cached features are on `self.device`). Pinning the
find-side tensors to the same device restores the `_get_img_feats` invariant.

`copy_data_to_device` (the upstream `sam3.model.utils.misc` helper) recurses the
`FindInput`/`FindTarget` structures, so every nested tensor — `img_ids`,
`input_boxes`, masks, labels — is moved, not just `img_ids`.

## Alternatives considered

- **Move the cached `backbone_out` to `img_ids`'s device** — wrong direction:
  the backbone features are large, `img_ids` is tiny, and `img_ids` may be on a
  device that disagrees with the model weights / decoder.
- **Move only `find_input.img_ids`** — fixes the observed crash but leaves other
  find-side tensors (`input_boxes`, used to build `geometric_prompt`) liable to
  the same mismatch if interactive steps run in eval mode.
- **Pin `bundle["device"]` harder upstream** — does not help: the divergence is
  the thread-local current device, not the bundle config.

## Scope

Only the cached fast path was missing the normalisation. The non-cached
`_run_text_prompts_batched` calls `model(batch)` straight after
`copy_data_to_device(batch, ...)` and routes through the unpatched upstream
forward, which normalises on its own — it is safe by construction.

## Cross-references

- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md)
- [inference/sam3-perf-profiling.md](../inference/sam3-perf-profiling.md)
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)
