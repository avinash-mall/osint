# `inference-sam3/sam3_runner.py` — SAM 3 Orchestration

**Path:** [inference-sam3/sam3_runner.py](../../inference-sam3/sam3_runner.py)
**Lines:** ~1613 (the largest inference-side file)
**Depends on:** `torch`, `transformers`, `huggingface_hub`, `sam3` (Meta SAM3 package)

## Purpose

Loads SAM 3 / SAM 3.1 image weights and runs the text- and box-prompted detection paths. Handles model selection (official vs mirror), device placement, batched text prompting, per-category threshold gating, and the category-presence gate ([why-category-presence-gate.md](../decisions/why-category-presence-gate.md)).

## Key symbols (loaders)

- [`build_image`](../../inference-sam3/sam3_runner.py#L222) — public entry for building an image bundle on a single device.
- [`_default_compile_video`](../../inference-sam3/sam3_runner.py#L28) — choose `torch.compile` strategy.
- [`_patch_pkg_resources_py312`](../../inference-sam3/sam3_runner.py#L149) — workaround for the `pkg_resources` deprecation in newer Python.
- [`_cuda_unsupported_arch_policy`](../../inference-sam3/sam3_runner.py#L166), [`_auto_cuda_devices`](../../inference-sam3/sam3_runner.py#L171), [`normalize_device_list`](../../inference-sam3/sam3_runner.py#L197), [`resolve_devices`](../../inference-sam3/sam3_runner.py#L206) — device-selection ladder (`DEVICE=auto` expands to a per-GPU list).

## Key symbols (text prompting + gating)

- [`_load_per_class_category_thresholds`](../../inference-sam3/sam3_runner.py#L62) — `SAM3_PER_CLASS_CATEGORY_THRESHOLDS` env JSON.
- [`_canonical_prompt_key`](../../inference-sam3/sam3_runner.py#L96) — same canonicalization as `backend.ontology._canonical`.
- [`_category_threshold_for`](../../inference-sam3/sam3_runner.py#L108) — per-class override or fall through to `SAM3_CATEGORY_THRESHOLD`.

## How a `/detect` call uses this module

1. `main.py` resolves the prompt list and the modality.
2. `sam3_runner.build_image(device)` returns a bundle dict cached per profile.
3. For text prompts: SAM3's `text_segment` runs per prompt → masks + scores. Category gate suppresses prompts with no plausible response (`max(score) < SAM3_CATEGORY_THRESHOLD`). Surviving masks filtered by `SAM3_TEXT_THRESHOLD`.
4. For box prompts: SAM3's box-prompted segmentation refines an upstream detector's ROI; threshold is `SAM3_BOX_THRESHOLD` (default 0.25, looser than text).
5. Output passed to [fusion.py](../../inference-sam3/fusion.py) for mask-aware NMS across layers.

## Cached-encoder fast path

`_run_text_prompts_cached_batched` runs the ViT encoder once per image, then iterates text prompts in chunks doing only text-encode + DETR-decode. It relies on the runtime patch [`patches/sam3_cached_forward.py`](../../inference-sam3/patches/sam3_cached_forward.py), which replaces `Sam3Image.forward` with `forward_with_cache` — that variant reuses a stashed `_cached_backbone_out` and skips the encoder.

`forward_with_cache` must mirror the *whole* of upstream `Sam3Image.forward`, including its normalisation of the datapoint device before grounding. It moves `input.find_inputs` / `input.find_targets` onto the device of the cached `backbone_out` via the local `_move_tensors_to_device` helper (the upstream `copy_data_to_device` does not recurse into `FindInput`). Omitting it crashes `_get_img_feats` on multi-GPU hosts when `find_input.img_ids` and the cached `vis_pos_enc` land on different CUDA devices. See [decisions/cached-forward-device-normalise.md](../decisions/cached-forward-device-normalise.md).

## Inputs / Outputs

Image paths return `(mask, bbox_xyxy, score, label)` tuples. The service entrypoint wraps those tuples with `source_layer` before fusion.

Video paths stream JSON records. SAM3 PCS records now include `source_layer="sam3"`; YOLOE tracker records include `source_layer="yoloe"` so the worker can persist detector provenance.

## Failure modes

The category-presence gate drops an entire prompt when all scores are below the configured threshold. Video category gating buffers through the hotstart window before either flushing or suppressing a prompt's stream.

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md)
- [fusion-and-nms.md](fusion-and-nms.md)
- [sam3-perf-profiling.md](sam3-perf-profiling.md)
- [decisions/why-sam3-as-foundation.md](../decisions/why-sam3-as-foundation.md)
- [decisions/why-category-presence-gate.md](../decisions/why-category-presence-gate.md)
- [decisions/cached-forward-device-normalise.md](../decisions/cached-forward-device-normalise.md)
