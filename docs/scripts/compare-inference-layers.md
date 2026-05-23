# `scripts/compare_inference_layers.py` — Main Benchmark CLI

**Path:** [scripts/compare_inference_layers.py](../../scripts/compare_inference_layers.py)

## Purpose

The main quality + latency benchmark. Sweeps a defined set of layer configurations (e.g. `sam3-only`, `sam3+dota`, `sam3+dota+gdino`, etc.) across one or more dataset slices; reports mAP + per-class P/R/F1 + latency tables.

## Usage

```bash
python scripts/compare_inference_layers.py \
  --url http://172.18.0.2:8001 \
  --slice all                                       # or dota, hls_burn, sen1floods, sar, embedding
  --max-chips 30 --repeats 3 \
  --output docs/benchmarks/inference-layer-comparison.md \
  --json-output docs/benchmarks/inference-layer-comparison.json \
  --restart-cmd "docker restart osint-inference-sam3-1" \
  --restart-wait-timeout 180 \
  --force-grounding-dino                            # disable the GDINO gate for the run
```

## Key flags

- `--restart-cmd` + `--restart-wait-timeout` — between configs, restart the inference container to free SAM3 VRAM cleanly. Required when switching profiles.
- `--force-grounding-dino` — disables [the gate](../inference/grounding-dino-gate.md) so the harness can measure GDINO's impact on common-vocab prompts.
- `--ontology-mode` (+ `--ontology-url`, `--ontology-branch`) — for the `dota` slice, replaces each chip's ground-truth class names (an oracle the operator never has) with the live ontology default-prompt vocabulary fetched from the backend. Measures detection quality the way an analyst actually sees it. `--ontology-branch` takes a comma-separated list of branch ids; the union of those branches' scoped subsets is used, modelling a scene-relevant vocabulary.
- `--dry-run` — verify report generation without a live service.

## Oracle prompts vs `--ontology-mode`

By default each DOTA chip is fed `text_prompts` = the exact GT class names present in that chip — a best-case oracle. `--ontology-mode` instead feeds every chip the full ontology vocabulary, so precision reflects the real false-positive rate when the model is asked about ~130 classes at once. The gap between the two runs is the cost of not knowing what is in the scene.

## Wrapper

[scripts/_eval_runner.py](../../scripts/_eval_runner.py) is a wrapper that filters out historically broken configurations (e.g. the Grounding-DINO instability workaround). Use it for the curated set.

## Cross-references

- [testing/benchmark-harness.md](../testing/benchmark-harness.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
- [decisions/why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md)
