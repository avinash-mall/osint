# Why RemoteCLIP verifier is default-on for the imagery profile

**Date:** 2026-05-28
**Affects:** [.env.example](../../.env.example), [docker-compose.yml](../../docker-compose.yml), [inference-sam3/main.py](../../inference-sam3/main.py), [docs/inference/remoteclip-verifier.md](../inference/remoteclip-verifier.md)

## Problem

T1.2 shipped a `label_quality_for()` promotion path that reads
`semantic_margin` from each detection. When the margin clears
`LABEL_VERIFIER_MARGIN_FLOOR`, the detection moves from `inferred` /
`generic` to `verified` and gets the `[VERIFIED]` chip in the UI. With
`SAM3_LOAD_REMOTECLIP=0` (the old default) nothing ever wrote
`semantic_margin`, so:

- Every SAM3 / Grounding-DINO detection stayed `inferred`.
- Every generic DOTA-OBB call stayed `generic` (no path to specific
  defence labels).
- The verifier-floor knob shipped in T1.2 was dead code in production.

The RemoteCLIP weights have been baked into the inference image since
[inference-sam3/Dockerfile.gpu#L102](../../inference-sam3/Dockerfile.gpu#L102),
and `REMOTECLIP_LOCAL_FILES_ONLY=1` keeps the loader air-gap-safe. The
only thing missing was the default flag.

## Decision

- Flip `SAM3_LOAD_REMOTECLIP` default `0 → 1` in
  [.env.example](../../.env.example),
  [docker-compose.yml](../../docker-compose.yml), and
  [inference-sam3/main.py](../../inference-sam3/main.py).
- Gate the per-detection verify call to a configurable allow-list of
  source layers. Default: `{sam3, grounding_dino}`. Operators extend the
  gate with the new `REMOTECLIP_VERIFIER_LAYERS` env (comma-separated).
- Cost: ~1.5 GB VRAM steady-state on the imagery profile and one extra
  CLIP forward per kept candidate from the gated layers.

## What was deliberately NOT done

- **DOTA-OBB is NOT verified.** Its closed-vocab 18 classes outperform
  RemoteCLIP's open-vocab text matching for those categories (0.60 mAP
  standalone). Second-guessing them would only add cost and risk
  demoting correct calls. Same logic as
  [why-grounding-dino-auto-gated.md](why-grounding-dino-auto-gated.md).
- **`REMOTECLIP_MARGIN_THRESHOLD` unchanged** (0.05). Operators tune
  promotion at the backend via `LABEL_VERIFIER_MARGIN_FLOOR` (T1.2).
- **FMV profile untouched.** Video tracking is GPU-tight and RemoteCLIP
  adds no value on a per-frame basis — tracker association is already
  using DINOv3-SAT embeddings.

## Measured impact

- VRAM: +~1.5 GB on the imagery profile (RemoteCLIP ViT-B-32).
- Latency: one extra CLIP forward per surviving SAM3/GDINO candidate;
  DOTA-OBB calls bypass the gate.
- Label quality: T1.2's `[VERIFIED]` chip now fires on the subset of
  SAM3/GDINO detections whose `semantic_margin` clears the floor —
  previously this code path was unreachable.

## Cross-references

- [inference/remoteclip-verifier.md](../inference/remoteclip-verifier.md)
- [inference/service-overview.md](../inference/service-overview.md)
- [decisions/why-grounding-dino-auto-gated.md](why-grounding-dino-auto-gated.md)
- [decisions/why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md)
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
