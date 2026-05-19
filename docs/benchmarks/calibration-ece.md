# Calibration ECE — Expected Calibration Error

**Source:** [scripts/measure_calibration_ece.py](../../scripts/measure_calibration_ece.py)
**Module:** [backend/calibration.py](../../backend/calibration.py)

## What it measures

For each detector (SAM3, DOTA-OBB, Grounding-DINO), bin predictions by confidence (e.g. 0-10%, 10-20%, ...) and measure the gap between mean confidence in the bin and the empirical accuracy in the bin. Lower ECE means the model's confidence is calibrated; high ECE means it's overconfident or underconfident.

## Output

- Markdown table with per-detector ECE pre/post temperature scaling.
- `model_temperatures.json` with the optimal per-detector temperature values.

## How to reproduce

```bash
python scripts/measure_calibration_ece.py \
  --dataset dota \
  --url http://172.18.0.2:8001 \
  --output bench/calibration_ece.md \
  --temperatures /data/calibration/model_temperatures.json
```

After running, copy the temperatures file to `/data/calibration/model_temperatures.json` for the backend to pick up.

## Cross-references

- [backend/calibration-temperature.md](../backend/calibration-temperature.md)
- [scripts/eval-runners.md](../scripts/eval-runners.md)
