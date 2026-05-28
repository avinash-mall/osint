# Why SAM 3 / SAM 3.1 as the Foundation Model

## Decision

The inference stack is built around **SAM 3** (image) and **SAM 3.1** (multiplex video) as the foundation — every other detector either prompts SAM3, post-processes its masks, or is gated against it.

## Why

- **Open-vocabulary segmentation** — SAM3's native API accepts free-text prompts, produces pixel masks. Closed-vocab image specialists such as DOTA-OBB emit boxes from a fixed list; SAM3 fills the gap for anything not in those lists.
- **Single API across modalities** — RGB, multispectral, SAR all go through SAM3: multispectral via Prithvi-derived RGB preview, SAR via TerraMind S1→S2 synthesis. Downstream record schema is identical regardless of sensor.
- **Mask + box + OBB in one pass** — SAM3 emits the mask; the worker derives a tight HBB + OBB via `cv2.minAreaRect` on the mask contour. No second model needed for oriented boxes (DOTA-OBB is additive, not required).
- **Native multiplex video** — SAM 3.1's `build_sam3_multiplex_video_predictor` tracks a single text prompt per session across frames. The worker fans out one request per prompt, merges streams → multi-class FMV tracking.

## Trade-offs accepted

- **Gated weights** — SAM3 weights live under `facebook/sam3` and `facebook/sam3.1` (gated; requires `HF_TOKEN` with approved access). A mirror at `1038lab/sam3` is supported via `SAM3_WEIGHTS_SOURCE=mirror`.
- **VRAM cost** — SAM3 image + SAM3.1 video is ~5-7 GB FP16; feasible on a 16 GB card (RTX 5070 Ti) but excludes Prithvi+TerraMind from that budget. See VRAM budget in [inference/service-overview.md](../inference/service-overview.md).
- **Cannot free without process restart** — the architectural reason inference runs as a separate container, see [component-boundaries.md](../architecture/component-boundaries.md) §2.

## Cross-references

- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md)
- [inference/sam3-pcs-multiplex-video.md](../inference/sam3-pcs-multiplex-video.md)
- [why-open-vocabulary.md](why-open-vocabulary.md)
