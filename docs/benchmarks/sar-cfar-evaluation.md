# SAR CFAR Evaluation

**Source:** [scripts/eval_sar_cfar.py](../../scripts/eval_sar_cfar.py)
**Dataset:** Synthetic 2-band dB-range TIFFs in `scripts/eval_datasets/sar_synth/` (real Sentinel-1 GRD slices can be substituted)
**Detector:** [backend/sar_cfar.py](../../backend/sar_cfar.py)

## What it measures

Precision/Recall of the CA-CFAR detector on synthetic SAR chips with known ship locations. Also reports detection count and score distribution — these are the noise-floor smoke checks for changes to the CFAR thresholds.

## How to reproduce

```bash
python scripts/eval_sar_cfar.py \
  --dataset scripts/eval_datasets/sar_synth \
  --output bench/sar_cfar_smoke.json
```

## Why synthetic

Real S1 GRD with annotated ship locations is hard to come by in volume. The synthetic data is a smoke check, not a quality certification — it catches accidental regressions to the detector when the CFAR window or threshold is tuned.

## Cross-references

- [backend/sar-cfar-detector.md](../backend/sar-cfar-detector.md)
- [inference/sar-bands.md](../inference/sar-bands.md)
- [scripts/eval-runners.md](../scripts/eval-runners.md)
