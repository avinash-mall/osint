# Batched text path postprocesses at a low gate floor (presence-gate parity)

**Date:** 2026-06-09
**Status:** adopted
**Affects:** [inference-sam3/sam3_runner.py](../../inference-sam3/sam3_runner.py)

## Decision

The batched SAM3 text path (`SAM3_BATCHED_TEXT=1`, the default production path)
now postprocesses detections at `SAM3_GATE_SCORE_FLOOR` (default **0.05**) so the
SegEarth-OV3 presence-ratio gate sees the full score distribution, and emitted
detections are filtered at the caller's `score_threshold` in
`_collect_batched_candidates`.

## Problem

The presence gate (`_prompt_passes_category_gate`, see
[why-segearth-presence-filter.md](why-segearth-presence-filter.md)) drops a prompt
when its score distribution is **diffuse** — `max/mean < SAM3_PRESENCE_RATIO_FLOOR`
(1.8). A real, sharply-localized detection has `max ≫ mean` (a long low tail drags
the mean down); a hallucination is `max ≈ mean`.

The **single-prompt** path runs the gate on the model's raw, unthresholded score
distribution (`processor.set_text_prompt(...)["scores"]` — all object queries).

The **batched** path built `PostProcessImage(detection_threshold=score_threshold)`
(≈0.50), so by the time `_collect_batched_candidates` ran the gate, every score
was already ≥ 0.50. The low tail was gone, the mean was compressed up toward the
max, and `max/mean` collapsed to ≈1.0 — **below 1.8** — so the gate wrongly dropped
real prompts. Because the batched path is the default, the production behaviour was
far more aggressive than the single-prompt unit tests implied.

## Fix

- Postprocess the batched path at `min(SAM3_GATE_SCORE_FLOOR, score_threshold)`
  instead of `score_threshold`, restoring the low tail the ratio gate needs.
- `_collect_batched_candidates(processed, query_labels, score_threshold)` now
  emit-filters at `score_threshold` (the single-prompt path does the equivalent in
  `_collect_candidates`), so only above-threshold detections are returned.

The existing `test_batched_path_applies_presence_gate` already encoded the intended
contract (the gate must see the full distribution); a new
`test_batched_path_gate_sees_full_distribution_then_emit_filters` locks in the
emit-filter.

## Why 0.05 and not 0.0

`0.0` is exact parity with the single-prompt path but interpolates a mask for every
near-zero object query — a latency cost on the default path. `0.05` keeps the
meaningful low tail (the junk scores that drag the mean down sit well above it)
while skipping the near-zero bulk. Operators wanting exact parity set
`SAM3_GATE_SCORE_FLOOR=0.0`; those who profile a regression can raise it.

## Validation note

This changes detection recall on the default path (more prompts survive the gate),
so a mAP + latency benchmark via
[scripts/compare_inference_layers.py](../../scripts/compare_inference_layers.py)
should confirm the precision/latency trade before a production rollout — the env
knob makes the floor tunable without a code change.

## Cross-references

- [why-segearth-presence-filter.md](why-segearth-presence-filter.md)
- [why-category-presence-gate.md](why-category-presence-gate.md)
- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md)
