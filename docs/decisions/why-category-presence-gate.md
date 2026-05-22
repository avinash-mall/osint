# Why a Category-Presence Gate

## Decision

`SAM3_CATEGORY_THRESHOLD=0.40` (default) applied **before** per-mask thresholding. Prompts whose best mask has weak presence are suppressed at the category level — eliminating hallucinated detections of absent concepts.

A SegEarth-OV-3-style filter.

## Why

SAM3 is open-vocab: a request `["car", "battleship", "submarine"]` on a chip containing only buildings and grass still makes the text encoder produce an attention map for "submarine." SAM3 then finds the highest-scoring region in that map, returns a low-confidence mask. Without filtering, this is a **hallucination** — a confident-looking submarine detection on a city block.

Fix: a presence-of-concept gate that looks at the maximum mask score per prompt class and drops the entire prompt before any per-mask thresholding. Concepts that aren't really there don't survive the gate.

## Behavior

```
for prompt in resolved_prompts:
    masks = sam3.text_segment(chip, prompt)
    best_score = max(m.score for m in masks)
    if best_score < SAM3_CATEGORY_THRESHOLD:
        continue   # this prompt produced no real detection
    detections.extend(filter(score >= SAM3_TEXT_THRESHOLD, masks))
```

The two thresholds differ on purpose:
- `SAM3_CATEGORY_THRESHOLD` (0.40) — "did SAM3 find anything plausible for this prompt at all?"
- `SAM3_TEXT_THRESHOLD` (0.50) — "is this individual mask confident enough to keep?"

A category survives the gate at 0.41 but its 0.49 best mask is still filtered out.

## When to disable

Set `SAM3_CATEGORY_THRESHOLD=0.0` to disable the gate. Useful for:
- Debugging why a class isn't appearing on a known-positive chip.
- Calibration experiments wanting the full attention-map distribution.

Production stays at 0.40.

## Cross-references

- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md)
- [why-open-vocabulary.md](why-open-vocabulary.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
