# Removed: DEFENCE_YOLO

## Status

Removed in v0.10.

## What it was

A YOLO variant fine-tuned on defense imagery, intended to flag damaged structures as a `battle_damage` class. Loaded via the (now-removed) `SAM3_LOAD_DEFENCE_YOLO` env flag.

## Why it was removed

On DOTA-v1.0 val (26 chips, ground-truth available):

| Detector | True positives | False positives |
|---|---|---|
| DEFENCE_YOLO (`battle_damage`) | **0** | **1297** |

Zero true positives, 1297 false positives across 26 chips. Fired on shadows, terrain texture, parked cars — anything with sharp edges and dark internal regions. Actively degraded mAP at every confidence threshold tested.

## Why it failed

Training data was thin, labels noisy → the model learned "high-contrast pattern" instead of "structural damage." Without a curated eval set, the failure was invisible at training time.

## Lesson

Before adding a new detector to the inference profile, run [`scripts/compare_inference_layers.py`](../scripts/compare-inference-layers.md) with the candidate enabled and disabled — anything that *only* adds false positives gets caught.

## Cross-references

- [conventions/adding-a-new-detection-model.md](../conventions/adding-a-new-detection-model.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
