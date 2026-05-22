# Why Grounding-DINO Is Auto-Gated

## Decision

Grounding-DINO runs **only** when the request expresses operator intent and the prompts are outside the "common vocab" (DOTA-v1 fixed classes ∪ SAM3 pretrained vocabulary ∪ backend ontology defaults). On common-vocab requests, the server-side gate skips GDINO entirely.

**Gate code:** [inference-sam3/grounding_dino_gate.py](../../inference-sam3/grounding_dino_gate.py).

## Why

The full benchmark (see [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)) shows: on DOTA-v1.0 val with common-vocab prompts (plane, ship, vehicle, etc.):

| Config | DOTA val mAP |
|---|---|
| DOTA-OBB alone | **0.61** |
| DOTA-OBB + Grounding-DINO (forced) | 0.11 |

GDINO's text-derived boxes overlap DOTA-OBB's correct detections. NMS keeps GDINO's lower-confidence call, discards DOTA-OBB's correct one. Net effect: **NMS suppression destroys mAP** when GDINO is forced on common-vocab prompts. With +115 ms additional latency, the cost is doubly bad.

When the prompt set contains words SAM3 + DOTA-OBB don't already cover, GDINO genuinely fills a gap. The gate lets the system have it both ways.

## How the gate works

For each request, the gate computes the intersection of the resolved prompts with:
- DOTA's 18 classes (plane, ship, vehicle, bridge, etc.)
- A maintained list of SAM3's strong pretrained labels
- The backend ontology's `default_prompts` for that sensor

If **every** prompt falls inside that union, GDINO is skipped. Otherwise it may run after the service confirms the layer was explicitly enabled (`enabled_layers` includes `grounding_dino`) or forced.

The decision is logged per-request → operators see in `/api/inference/dashboard` how often GDINO is firing.

## Operator override

Set `metadata.force_grounding_dino=true` to bypass the gate for a single request. The benchmark harness uses `--force-grounding-dino` to confirm the gate's value is real.

## Cross-references

- [inference/grounding-dino-gate.md](../inference/grounding-dino-gate.md)
- [inference/grounding-dino-detector.md](../inference/grounding-dino-detector.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
