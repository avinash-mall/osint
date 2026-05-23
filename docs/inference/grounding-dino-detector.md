# `inference-sam3/grounding_dino.py` — Grounding-DINO Detector

**Path:** [inference-sam3/grounding_dino.py](../../inference-sam3/grounding_dino.py)
**Lines:** ~226
**Depends on:** `transformers` (IDEA-Research Grounding-DINO tiny/base)

## Purpose

Open-vocabulary text-to-box detector. Runs **only** when the gate ([grounding-dino-gate.md](grounding-dino-gate.md)) allows it **and** the operator explicitly enables/forces the layer (`enabled_layers` includes `grounding_dino`, or `force_grounding_dino=true`). Fallback for prompts outside the common vocab.

## Key symbols

- [`load`](../../inference-sam3/grounding_dino.py#L42) — builds the GDINO bundle on a device.
- [`run`](../../inference-sam3/grounding_dino.py#L68) — text + image → `(mask, bbox_xyxy, score, label)` tuples (mask is a bbox-mask, not real segmentation). Splits the vocabulary into `GROUNDING_DINO_MAX_PHRASES_PER_QUERY`-sized chunks (default 10) and runs one forward pass per chunk.
- `_forward_chunk` — one GDINO forward pass over a single chunk of phrases, with the OOM/memory guard.
- [`_map_to_original_prompt`](../../inference-sam3/grounding_dino.py#L174) — GDINO returns its own canonicalized labels; maps them back to the operator's input prompt strings.
- [`_bbox_mask`](../../inference-sam3/grounding_dino.py#L207) — synthetic rectangular mask for SAM3-aware downstream code.
- [`model_versions`](../../inference-sam3/grounding_dino.py#L216) — exposed in `/health`.

## Why chunked queries

GDINO's processor joins all phrases into one `". "`-separated caption. A long caption makes adjacent concepts "bleed" into each other's token spans, inflating false positives — the dominant failure mode for open-vocabulary detectors on overhead imagery. Chunking caps each query at ~10 phrases; detections from every chunk merge in [`fusion.mask_aware_nms`](fusion-and-nms.md), so chunking is transparent to callers. Thresholds were also firmed: box `GROUNDING_DINO_THRESHOLD=0.30` (was 0.20), text `GROUNDING_DINO_TEXT_THRESHOLD=0.25` (was 0.15). See [decisions/why-deconflicted-detection-prompts.md](../decisions/why-deconflicted-detection-prompts.md).

## Why a bbox-mask?

GDINO emits boxes, not masks; the rest of the pipeline (fusion, NMS, OBB extraction) wants masks. The bbox-mask is a filled rectangle covering the GDINO box — works for IoU-based NMS but no pixel-level outlines. That's why the gate matters: on a common-vocab prompt, SAM3's real mask beats GDINO's box.

## Inputs / Outputs

Inputs: image chips + explicit text prompts. Outputs: SAM3-shaped `(mask, bbox_xyxy, score, label)` tuples; service entrypoint tags them `source_layer="grounding_dino"` before NMS and response serialization.

## Failure modes

Detector skipped unless both operator intent and uncommon-prompt gating agree. Forced runs are for experiments and can still degrade DOTA-OBB quality through NMS competition.

## Cross-references

- [grounding-dino-gate.md](grounding-dino-gate.md)
- [decisions/why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md)
- [decisions/why-deconflicted-detection-prompts.md](../decisions/why-deconflicted-detection-prompts.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
