# SAM 3.1 PCS — Multiplex Video Predictor

**Path:** [inference-sam3/sam3_runner.py](../../inference-sam3/sam3_runner.py) (video predictor build/run)
**Used by:** `/detect_video` handler in [inference-sam3/main.py](../../inference-sam3/main.py)

## Purpose

Track a **single text prompt** across video frames using SAM 3.1's `build_sam3_multiplex_video_predictor`. "Multiplex" = internally multiplexing across heads to produce stable per-frame masks for one session.

## Why this design

- **One session = one prompt** — multiplex predictor engineered to track a single text prompt with high temporal stability. Multi-class tracking handled at the worker level: backend worker fans out one request per prompt, merges streams.
- **One prompt per session, many frames** — session built once per call, reused across the whole clip → much faster than per-frame re-segmentation.
- **NDJSON streaming** — worker can begin persisting detections before the inference call finishes; important for clips taking minutes.

## Operator-facing contract

`POST /detect_video` with:

```json
{
  "metadata": {
    "prompt_mode": "pcs",
    "text_prompts": ["a person"],
    "frame_stride": 2
  }
}
```

First prompt = session prompt; **additional prompts in the list ignored at the inference layer**. The worker (not inference) handles multi-prompt by calling `/detect_video` once per prompt, stitching NDJSON outputs by `frame_index`.

Each emitted record includes `source_layer="sam3"`. YOLOE alternative emits `source_layer="yoloe"`; worker persists that provenance in `fmv_detections.metadata`.

## Failure modes

Inference endpoint rejects multi-prompt PCS requests with HTTP 400 — upstream predictor resets state on every text prompt. Empty PCS prompts no-op; backend worker supplies a bounded default prompt list when an upload omitted prompts.

## Performance knobs

- `SAM3_USE_MULTIPLEX=1` — switches between SAM 3 and SAM 3.1 multiplex.
- `SAM3_COMPILE_VIDEO=0|1` — torch.compile the video predictor (slow first call, faster steady state).
- `SAM3_WARM_UP_VIDEO=1` — one-frame priming after load; reduces first-real-call latency.

## Cross-references

- [yoloe-tracker.md](yoloe-tracker.md) — the alternative tracker
- [decisions/why-yoloe-replaced-amg.md](../decisions/why-yoloe-replaced-amg.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md)
