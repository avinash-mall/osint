# `inference-sam3/grounding_dino.py` — Grounding-DINO Detector

**Path:** [inference-sam3/grounding_dino.py](../../inference-sam3/grounding_dino.py)
**Lines:** ~217
**Depends on:** `transformers` (IDEA-Research Grounding-DINO tiny/base)

## Purpose

Open-vocabulary text-to-box detector. Runs **only** when the gate ([grounding-dino-gate.md](grounding-dino-gate.md)) allows it **and** the operator explicitly enables/forces the layer (`enabled_layers` includes `grounding_dino`, or `force_grounding_dino=true`). Fallback for prompts outside the common vocab.

## Key symbols

- [`load`](../../inference-sam3/grounding_dino.py#L36) — builds the GDINO bundle on a device.
- [`run`](../../inference-sam3/grounding_dino.py#L62) — text + image → `(mask, bbox_xyxy, score, label)` tuples (mask is a bbox-mask, not real segmentation).
- [`_map_to_original_prompt`](../../inference-sam3/grounding_dino.py#L165) — GDINO returns its own canonicalized labels; maps them back to the operator's input prompt strings.
- [`_bbox_mask`](../../inference-sam3/grounding_dino.py#L198) — synthetic rectangular mask for SAM3-aware downstream code.
- [`model_versions`](../../inference-sam3/grounding_dino.py#L207) — exposed in `/health`.

## Why a bbox-mask?

GDINO emits boxes, not masks; the rest of the pipeline (fusion, NMS, OBB extraction) wants masks. The bbox-mask is a filled rectangle covering the GDINO box — works for IoU-based NMS but no pixel-level outlines. That's why the gate matters: on a common-vocab prompt, SAM3's real mask beats GDINO's box.

## Inputs / Outputs

Inputs: image chips + explicit text prompts. Outputs: SAM3-shaped `(mask, bbox_xyxy, score, label)` tuples; service entrypoint tags them `source_layer="grounding_dino"` before NMS and response serialization.

## Failure modes

Detector skipped unless both operator intent and uncommon-prompt gating agree. Forced runs are for experiments and can still degrade DOTA-OBB quality through NMS competition.

## Cross-references

- [grounding-dino-gate.md](grounding-dino-gate.md)
- [decisions/why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
