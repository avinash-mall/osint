# Fixtures & Test Data

## Locations

- [tests/fixtures/](../../tests/fixtures/) — top-level fixtures consumed by both backend and scripts.
- [sample/](../../sample/) — sample MP4s (drone videos) used by `video_tracking_stability.py` and Playwright visual tests.
- [scripts/eval_datasets/](../../scripts/eval_datasets/) — dataset loaders for DOTA, Sen1Floods11, S1-GRD, HLS-burn, SAR-synth.
- [bench/](../../bench/) — committed benchmark output JSON (referenced by [benchmarks/](../benchmarks/) docs).

## Synthetic vs real

- **`scripts/fetch_real_datasets.py`** — downloads real DOTA val + Sen1Floods11. Requires `HF_TOKEN` and outbound network.
- **`scripts/fetch_eval_datasets.py --synthetic-fixtures`** — deterministic synthetic fixtures suitable for CI / smoke tests when the network is unavailable.

The fetch scripts are **idempotent** and gated by a `labels.json` marker that records which datasets are real vs synthetic — re-running won't re-download.

## What's safe to regenerate

| Path | Safe to regenerate? |
|---|---|
| `sample/*.mp4` | Yes — re-fetch from the documented sources |
| `tests/fixtures/*` | Yes — committed only to make tests reproducible |
| `bench/*.json` | No — these are historical benchmark snapshots; never overwrite without saving the previous result |
| `scripts/eval_datasets/sar_synth/*.tif` | Yes — synthesizer is deterministic |

## Cross-references

- [scripts/fetch-eval-datasets.md](../scripts/fetch-eval-datasets.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
- [benchmarks/sam3-perf-phases.md](../benchmarks/sam3-perf-phases.md)
