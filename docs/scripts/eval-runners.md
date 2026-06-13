# Eval Runners — Candidate Links, SAR-CFAR, Calibration

**Paths:**
- [scripts/eval_candidate_links.py](../../scripts/eval_candidate_links.py)
- [scripts/eval_sar_cfar.py](../../scripts/eval_sar_cfar.py)
- [scripts/measure_calibration_ece.py](../../scripts/measure_calibration_ece.py)

## eval_candidate_links.py

Tests the candidate-link scorer ([backend/candidate-linking.md](../backend/candidate-linking.md)) against curated ground-truth pairs. No live backend needed — directly imports `backend.candidate_linking.rank_candidate_links`, runs against `scripts/eval_datasets/candidate_links_gt.json`.

```bash
python scripts/eval_candidate_links.py --gt scripts/eval_datasets/candidate_links_gt.json
```

Reports precision/recall/F1 of "top-1 candidate is the correct target."

## eval_sar_cfar.py

Smoke test for [backend/sar-cfar-detector.md](../backend/sar-cfar-detector.md). Runs CFAR on the synthetic SAR fixture set, reports detection count + score distribution.

```bash
python scripts/eval_sar_cfar.py --dataset scripts/eval_datasets/sar_synth
```

## measure_calibration_ece.py

For each detector (SAM3, DOTA-OBB, GDINO), bin detections by confidence and measure ECE. Fits per-detector temperatures via minimization. Writes `model_temperatures.json` consumed by [backend/calibration-temperature.md](../backend/calibration-temperature.md).

```bash
python scripts/measure_calibration_ece.py \
  --dataset dota \
  --url http://172.18.0.2:8001 \
  --output bench/calibration_ece.md \
  --temperatures /data/calibration/model_temperatures.json
```

## Eval helpers

- [scripts/eval_datasets/](../../scripts/eval_datasets/) — slice-specific loaders (`dota.py`, `sentinel1.py`, `sar_synth.py`, `triage.py`)
- [scripts/eval_metrics/](../../scripts/eval_metrics/) — box AP/AR/F1 (`box_metrics.py`), label normalization (`label_normalizer.py`)
- [scripts/_eval_runner.py](../../scripts/_eval_runner.py) — curated wrapper around `compare_inference_layers.py`

## Cross-references

- [testing/benchmark-harness.md](../testing/benchmark-harness.md)
- [benchmarks/calibration-ece.md](../benchmarks/calibration-ece.md)
- [benchmarks/sar-cfar-evaluation.md](../benchmarks/sar-cfar-evaluation.md)
- [backend/candidate-linking.md](../backend/candidate-linking.md)
