# Why YOLOE Replaced SAM3 AMG for FMV

## Decision

**Removed:** SAM 3 AMG (`Sam3AutomaticMaskGenerator`) FMV path.
**Replacement:** YOLOE-26x-seg (text-promptable) and YOLOE-26x-seg-pf (prompt-free).

## Why

- **AMG required a second model.** AMG produces masks but no labels. To get class names, every mask had to be re-classified by Grounding-DINO. That's two model invocations per chip and one of them (GDINO) is unstable at scale — see [why-grounding-dino-auto-gated.md](why-grounding-dino-auto-gated.md).
- **YOLOE emits labels directly.** The `-pf` head ships a baked-in prompt-free vocabulary; the `-seg` head accepts text prompts and emits `(mask, bbox, label, score)` in one forward pass.
- **Latency.** YOLOE is comparable to SAM 3.1 PCS but eliminates the GDINO labeling pass. Per-frame cost roughly halves.

## Trade-offs accepted

- **YOLOE is AGPL-3.0.** Weights are open but the license is more restrictive than SAM3's research license + commercial workflow. See [licenses](../../README.md#licenses).
- **Vocabulary is bounded by training data.** Unlike SAM3 + open text, YOLOE-pf has a fixed-class baked-in set. We mitigate by exposing `-seg` (text) as the primary mode and `-pf` as the explicit prompt-free fallback when the operator wants generic boxes.

## Operator-visible change

`metadata.prompt_mode` on `/detect_video`:

| Mode | Engine | Behavior |
|---|---|---|
| `pcs` *(default)* | SAM 3.1 multiplex | Single-prompt-per-session text-prompted tracker |
| `yoloe` | YOLOE-26x-seg(-pf) | Standalone tracker; empty `text_prompts` → `-pf` |

The deprecated `prompt_mode=amg` is gone. Anything still sending it gets HTTP 400.

## Cross-references

- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md)
- [inference/sam3-pcs-multiplex-video.md](../inference/sam3-pcs-multiplex-video.md)
- [why-grounding-dino-auto-gated.md](why-grounding-dino-auto-gated.md)
