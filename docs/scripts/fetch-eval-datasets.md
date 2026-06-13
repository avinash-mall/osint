# Dataset Fetch Scripts

**Paths:**
- [scripts/fetch_real_datasets.py](../../scripts/fetch_real_datasets.py)
- [scripts/fetch_eval_datasets.py](../../scripts/fetch_eval_datasets.py)

## Purpose

Idempotent fetchers for the evaluation datasets used by [testing/benchmark-harness.md](../testing/benchmark-harness.md):

- **`fetch_real_datasets.py`** — downloads the real DOTA-v1.0 val slice from HuggingFace. Requires `HF_TOKEN`.
- **`fetch_eval_datasets.py`** — ensures the DOTA slice has either a real or a synthetic fixture available. `--synthetic-fixtures` for deterministic test-only fixtures.

## Why two scripts

The two-stage design lets CI runs without network use synthetic fixtures, while real benchmark runs use real data. The marker file (`labels.json`) tracks which slices are real vs synthetic → re-runs are idempotent.

## Usage

```bash
# Production / benchmark
export HF_TOKEN=hf_xxxxx
python scripts/fetch_real_datasets.py
python scripts/fetch_eval_datasets.py

# CI / smoke (no network)
python scripts/fetch_eval_datasets.py --synthetic-fixtures
```

## Output

- `scripts/eval_datasets/dota/` (DOTA val chips)
- `scripts/eval_datasets/sar_synth/` (synthetic 2-band TIFFs)
- `scripts/eval_datasets/labels.json` (manifest)

## Cross-references

- [testing/benchmark-harness.md](../testing/benchmark-harness.md)
- [testing/fixtures-and-test-data.md](../testing/fixtures-and-test-data.md)
- [compare-inference-layers.md](compare-inference-layers.md)
