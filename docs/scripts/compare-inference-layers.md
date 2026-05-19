# `scripts/compare_inference_layers.py` — Main Benchmark CLI

**Path:** [scripts/compare_inference_layers.py](../../scripts/compare_inference_layers.py)

## Purpose

The main quality + latency benchmark. Sweeps a defined set of layer configurations (e.g. `sam3-only`, `sam3+dota`, `sam3+dota+gdino`, etc.) across one or more dataset slices and reports mAP + per-class P/R/F1 + latency tables.

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
- `--dry-run` — verify report generation without a live service.

## Wrapper

[scripts/_eval_runner.py](../../scripts/_eval_runner.py) is a wrapper that filters out historically broken configurations (e.g. the Grounding-DINO instability workaround). Use it when you want the curated set.

## Cross-references

- [testing/benchmark-harness.md](../testing/benchmark-harness.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
- [decisions/why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md)
