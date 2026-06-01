# `inference-sam3/sam3_runner.py` — SAM 3 Orchestration

**Path:** [inference-sam3/sam3_runner.py](../../inference-sam3/sam3_runner.py)
**Lines:** ~1685 (largest inference-side file)
**Depends on:** `torch`, `transformers`, `huggingface_hub`, `sam3` (Meta SAM3 package)

## Purpose

Loads SAM 3 / SAM 3.1 image weights, runs text- and box-prompted detection paths. Handles model selection (official vs mirror), device placement, batched text prompting, per-category threshold gating, the category-presence gate ([why-category-presence-gate.md](../decisions/why-category-presence-gate.md)) — extended with the SegEarth-OV3-style score-distribution presence-ratio filter ([why-segearth-presence-filter.md](../decisions/why-segearth-presence-filter.md)).

## Key symbols (loaders)

- [`build_image`](../../inference-sam3/sam3_runner.py#L222) — public entry, builds an image bundle on a single device.
- [`_default_compile_video`](../../inference-sam3/sam3_runner.py#L28) — choose `torch.compile` strategy.
- [`_patch_pkg_resources_py312`](../../inference-sam3/sam3_runner.py#L149) — workaround for `pkg_resources` deprecation in newer Python.
- [`_cuda_unsupported_arch_policy`](../../inference-sam3/sam3_runner.py#L166), [`_auto_cuda_devices`](../../inference-sam3/sam3_runner.py#L171), [`normalize_device_list`](../../inference-sam3/sam3_runner.py#L197), [`resolve_devices`](../../inference-sam3/sam3_runner.py#L206) — device-selection ladder (`DEVICE=auto` expands to a per-GPU list).

## Key symbols (text prompting + gating)

- [`SAM3_PRESENCE_RATIO_FLOOR`, `SAM3_PRESENCE_RATIO_EPS`, `SAM3_PRESENCE_MODE`](../../inference-sam3/sam3_runner.py#L61-L79) — SegEarth-OV3-inspired score-distribution gate constants. Defaults `1.8 / 0.05 / "both"`. Mode `both` requires the legacy max-score gate AND the new presence-ratio gate to pass; `max` reverts to legacy behaviour; `ratio` runs only the new gate. See [why-segearth-presence-filter.md](../decisions/why-segearth-presence-filter.md).
- [`_load_per_class_category_thresholds`](../../inference-sam3/sam3_runner.py#L82) — `SAM3_PER_CLASS_CATEGORY_THRESHOLDS` env JSON.
- [`_canonical_prompt_key`](../../inference-sam3/sam3_runner.py#L116) — same canonicalization as `backend.ontology._canonical`.
- [`_category_threshold_for`](../../inference-sam3/sam3_runner.py#L131) — per-class override or fall through to `SAM3_CATEGORY_THRESHOLD`.
- [`_presence_signals`](../../inference-sam3/sam3_runner.py#L815) — pure helper returning `{"max", "mean", "ratio", "n"}` summary of a score distribution; used by tests and available for diagnostic logging.
- [`_prompt_passes_category_gate`](../../inference-sam3/sam3_runner.py#L832) — three-mode (`max` / `ratio` / `both`) presence gate; called once per prompt inside `_run_text_prompts`.
- [`_device_ctx`](../../inference-sam3/sam3_runner.py#L737) — pins PyTorch thread-local current CUDA device to the replica's device for the duration of a forward; `nullcontext()` on CPU. Outermost context in every inference `with` stack.

## How a `/detect` call uses this module

1. `main.py` resolves the prompt list + modality.
2. `sam3_runner.build_image(device)` returns a bundle dict cached per profile.
3. Text prompts: SAM3's `text_segment` runs per prompt → masks + scores. Category gate suppresses prompts with no plausible response (`max(score) < SAM3_CATEGORY_THRESHOLD`). Surviving masks filtered by `SAM3_TEXT_THRESHOLD`.
4. Box prompts: SAM3's box-prompted segmentation refines an upstream detector's ROI; threshold `SAM3_BOX_THRESHOLD` (default 0.25, looser than text).
5. Output → [fusion.py](../../inference-sam3/fusion.py) for mask-aware NMS across layers.

## Cached-encoder fast path

`_run_text_prompts_cached_batched` runs the ViT encoder once per image, then iterates text prompts in chunks doing only text-encode + DETR-decode. Relies on the runtime patch [`patches/sam3_cached_forward.py`](../../inference-sam3/patches/sam3_cached_forward.py), which replaces `Sam3Image.forward` with `forward_with_cache` — that variant reuses a stashed `_cached_backbone_out`, skips the encoder.

All four inference `with` stacks (`run_text_prompts`, `_run_text_prompts_batched`, `_run_text_prompts_cached_batched`, `run_box_prompts`) open with `_device_ctx(device)` as the outermost context. The SAM3 collator builds index tensors (notably `find_input.img_ids`) on PyTorch's *current* CUDA device, which is thread-local and drifts across replicas in the anyio threadpool; pinning it makes the collator place those tensors on the replica's GPU. First line of defence against the multi-GPU `_get_img_feats` crash; on a real 4-GPU host this proved necessary but not sufficient — the find-side co-location below is what conclusively fixes it.

Per-chunk body of `_run_text_prompts_cached_batched` is wrapped in `try/except`: a failing chunk (e.g. GPU OOM) is logged via `logger.warning` and skipped → the tile still returns detections from the chunks that succeeded instead of 500-ing the whole `/detect_raw` request. **But if *every* chunk fails** (e.g. the GPU profile over-committed VRAM so no SAM3 forward fits), it raises a `RuntimeError` instead of returning an empty list — so `/detect` returns non-200 and the worker fails the upload rather than masking a real misconfig as "no objects found." See [decisions/why-dynamic-modality-loading-on-tight-vram.md](../decisions/why-dynamic-modality-loading-on-tight-vram.md).

`forward_with_cache` co-locates `find_input` / `find_target` (each a SAM3 `FindStage`) onto the device of the cached `backbone_out` before grounding. It calls upstream `copy_data_to_device` **per-stage** — that helper is dataclass-aware and returns a moved copy, so `find_input.img_ids` lands on the indexed features' device. (Calling it on the whole batch never reached the stages — the batch's top type isn't a dataclass; and the old in-place `_move_tensors_to_device` mutation silently no-op'd on the stages — retained now only as a backstop for non-dataclass stage types.) A one-shot `_log_device_normalise_once` records `img_ids`'s device before/after. This is the layer that actually fixes the multi-GPU `_get_img_feats` crash; `_device_ctx` is the first line of defence. See [decisions/cached-forward-device-normalise.md](../decisions/cached-forward-device-normalise.md).

## Inputs / Outputs

Image paths return `(mask, bbox_xyxy, score, label)` tuples. Service entrypoint wraps those with `source_layer` before fusion.

Video paths stream JSON records. SAM3 PCS records include `source_layer="sam3"`; YOLOE tracker records include `source_layer="yoloe"` → worker persists detector provenance.

## Failure modes

Category-presence gate drops an entire prompt when all scores are below the configured threshold. Default `SAM3_PRESENCE_MODE=both` adds a SegEarth-OV3-style ratio check on top — prompts with a diffuse score distribution (`max / mean < SAM3_PRESENCE_RATIO_FLOOR`, default `1.8`) are also dropped. Operators wanting strict legacy behaviour set `SAM3_PRESENCE_MODE=max`. Video category gating buffers through the hotstart window before either flushing or suppressing a prompt's stream.

Before `_device_ctx` pinning: multi-GPU hosts failed ~half of `/detect_raw` tiles with `RuntimeError: indices should be either on cpu or on the same device as the indexed tensor` in `_get_img_feats` — the collated `img_ids` landed on a drifted current CUDA device. Detections then covered only the region whose tiles happened to route to a matching replica. A single chunk failure inside the cached-batched loop is now contained per-chunk rather than failing the whole tile.

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md)
- [fusion-and-nms.md](fusion-and-nms.md)
- [sam3-perf-profiling.md](sam3-perf-profiling.md)
- [decisions/why-sam3-as-foundation.md](../decisions/why-sam3-as-foundation.md)
- [decisions/why-category-presence-gate.md](../decisions/why-category-presence-gate.md)
- [decisions/why-segearth-presence-filter.md](../decisions/why-segearth-presence-filter.md)
- [decisions/cached-forward-device-normalise.md](../decisions/cached-forward-device-normalise.md)
