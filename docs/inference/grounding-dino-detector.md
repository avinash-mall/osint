# `inference-sam3/grounding_dino.py` — `grounding_dino` Layer (LAE-DINO HTTP client)

**Path:** [inference-sam3/grounding_dino.py](../../inference-sam3/grounding_dino.py)
**Lines:** ~210
**Depends on:** `requests`, `PIL` + the `inference-lae` sidecar (LAE-DINO). No in-process ML deps.

## Purpose

Open-vocabulary text-to-box detector layer. The model behind it is now
**LAE-DINO** (remote-sensing-tuned Grounding-DINO derivative) running in the
separate [inference-lae](lae-dino-sidecar.md) service; this module is the HTTP
client. Runs **only** when the gate ([grounding-dino-gate.md](grounding-dino-gate.md))
allows it **and** the operator explicitly enables/forces the layer
(`enabled_layers` includes `grounding_dino`, or `force_grounding_dino=true`).
The layer key stays `grounding_dino` — see
[decisions/why-lae-dino-replaces-grounding-dino.md](../decisions/why-lae-dino-replaces-grounding-dino.md)
for why the name is kept while the model changed.

## Key symbols

- [`load`](../../inference-sam3/grounding_dino.py#L51) — probes the sidecar `/health` and returns a bundle (`model=True` sentinel when reachable, `None`+error otherwise). No GPU work in this process.
- [`run`](../../inference-sam3/grounding_dino.py#L83) — text + image → `(mask, bbox_xyxy, score, label)` tuples. PNG-encodes the chip once, POSTs `/detect` per `GROUNDING_DINO_MAX_PHRASES_PER_QUERY`-sized chunk (default 10).
- `_forward_chunk` — one `/detect` HTTP call over a single chunk of phrases. (A batched `run_batch` variant was evaluated and removed — see [decisions/why-lae-cross-chip-batching.md](../decisions/why-lae-cross-chip-batching.md).)
- [`_map_to_original_prompt`](../../inference-sam3/grounding_dino.py#L163) — LAE-DINO returns matched entity strings; maps them back to the operator's input prompt strings (drops unmappable labels).
- [`_bbox_mask`](../../inference-sam3/grounding_dino.py#L196) — synthetic rectangular mask for SAM3-aware downstream code.
- [`model_versions`](../../inference-sam3/grounding_dino.py#L205) — exposed in `/health` (reports the LAE-DINO model id + sidecar url).

## Why chunked queries

A long `'.'`-separated caption makes adjacent concepts "bleed" into each other's
token spans, inflating false positives — the dominant failure mode for
open-vocabulary detectors on overhead imagery. Chunking caps each request at ~10
phrases; detections from every chunk merge in
[`fusion.mask_aware_nms`](fusion-and-nms.md), so chunking is transparent.
Thresholds are LAE-DINO's: box `GROUNDING_DINO_THRESHOLD=0.30`, text
`GROUNDING_DINO_TEXT_THRESHOLD=0.25`.

## Why a bbox-mask?

LAE-DINO emits boxes, not masks; the rest of the pipeline (fusion, NMS, OBB
extraction) wants masks. The bbox-mask is a filled rectangle covering the box —
works for IoU-based NMS but no pixel-level outlines. That's why the gate matters:
on a common-vocab prompt, SAM3's real mask beats a box. The "tight box from
LAE-DINO, pixel-perfect mask from SAM 3" pairing is the intended use.

## Inputs / Outputs

Inputs: image chips + explicit text prompts (+ `LAE_DINO_URL`, default
`http://inference-lae:8010`). Outputs: SAM3-shaped `(mask, bbox_xyxy, score, label)`
tuples; service entrypoint tags them `source_layer="grounding_dino"` before NMS
and response serialization.

## Failure modes

Detector skipped unless both operator intent and uncommon-prompt gating agree.
If the sidecar is down/unbuilt, `load()` returns `model=None` and the layer
no-ops (graceful). Forced runs are for experiments and can still degrade
DOTA-OBB quality through NMS competition.

## Cross-references

- [lae-dino-sidecar.md](lae-dino-sidecar.md) — the service this calls
- [grounding-dino-gate.md](grounding-dino-gate.md)
- [decisions/why-lae-dino-replaces-grounding-dino.md](../decisions/why-lae-dino-replaces-grounding-dino.md)
- [decisions/why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md)
- [decisions/why-deconflicted-detection-prompts.md](../decisions/why-deconflicted-detection-prompts.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
