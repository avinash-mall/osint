# Why YOLOE Replaced SAM3 AMG for FMV

## Decision

**Removed:** SAM 3 AMG (`Sam3AutomaticMaskGenerator`) FMV path.
**Replacement:** YOLOE-26x-seg (text-promptable) and YOLOE-26x-seg-pf (prompt-free).

## Why

- **AMG required a second model** — AMG produces masks but no labels. Getting class names meant re-classifying every mask by Grounding-DINO: two model invocations per chip, one of them (GDINO) unstable at scale — see [removed-grounding-dino-lae.md](removed-grounding-dino-lae.md).
- **YOLOE emits labels directly** — the `-pf` head ships a baked-in prompt-free vocabulary; the `-seg` head accepts text prompts and emits `(mask, bbox, label, score)` in one forward pass.
- **Latency** — YOLOE is comparable to SAM 3.1 PCS but eliminates the GDINO labeling pass. Per-frame cost roughly halves.

## Trade-offs accepted

- **YOLOE is AGPL-3.0** — weights open but the license is more restrictive than SAM3's research license + commercial workflow. See [licenses](../../README.md#licenses).
- **Vocabulary bounded by training data** — unlike SAM3 + open text, YOLOE-pf has a fixed-class baked-in set. Mitigated by exposing `-seg` (text) as the primary mode, `-pf` as the explicit prompt-free fallback when the operator wants generic boxes.

## Operator-visible change

`metadata.prompt_mode` on direct inference `/detect_video`:

| Mode | Engine | Behavior |
|---|---|---|
| `pcs` *(default)* | SAM 3.1 multiplex | Single-prompt-per-session text-prompted tracker |
| `yoloe` | YOLOE-26x-seg(-pf) | Standalone tracker; empty `text_prompts` → `-pf` |

The deprecated inference-level `prompt_mode=amg` is gone. Anything sending it
directly to `/detect_video` gets HTTP 400.

Backend upload compatibility is intentionally narrower: `POST /api/fmv/clips`
and `POST /api/ingest/upload` still accept `model=yolo26&prompt_mode=amg` as a
legacy UI/API alias for YOLOE prompt-free FMV. The backend maps that pair to the
worker's `yoloe` mode and sends inference `prompt_mode=yoloe`.

## Cross-references

- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md)
- [inference/sam3-pcs-multiplex-video.md](../inference/sam3-pcs-multiplex-video.md)
- [removed-grounding-dino-lae.md](removed-grounding-dino-lae.md)
