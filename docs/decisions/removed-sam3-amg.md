# Removed: SAM3 AMG (Automatic Mask Generator)

## Status

Removed in v0.10. Replaced by YOLOE-26x-seg(-pf).

## What it was

`Sam3AutomaticMaskGenerator` — SAM3's per-pixel point-prompted mask generator. Emits masks across a regular grid of point prompts without any text. Used in the FMV path to find tracks when the operator hadn't supplied text prompts.

## Why it was removed

AMG produces masks but **no labels**. Getting class names for the masks needed re-classification by a second model (Grounding-DINO). That meant:

1. SAM3 AMG forward pass (slow, dense grid).
2. Grounding-DINO classification pass on each mask (unstable — see [removed-grounding-dino-lae.md](removed-grounding-dino-lae.md)).
3. NMS / fusion between AMG masks and DOTA-OBB boxes.

YOLOE-26x-seg(-pf) does all three in one forward pass — emits `(mask, bbox, label, score)` natively. The `-pf` head is the prompt-free replacement; the `-seg` head replaces AMG + text prompts.

## Migration

Anything still sending `prompt_mode=amg` directly to `/detect_video` gets HTTP 400 from inference-sam3. Use `prompt_mode=yoloe` instead:

- Empty `text_prompts` → YOLOE-26x-seg-pf (prompt-free)
- Non-empty `text_prompts` → YOLOE-26x-seg (text-promptable)

The backend upload API keeps a compatibility alias for older clients:
`model=yolo26 + prompt_mode=amg` is accepted for FMV uploads and mapped to the
worker's `yoloe` mode before inference is called. `model=sam3 + prompt_mode=amg`
still returns HTTP 400.

## Cross-references

- [why-yoloe-replaced-amg.md](why-yoloe-replaced-amg.md)
- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md)
