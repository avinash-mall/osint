# Calibration ECE — Expected Calibration Error

**Source:** [scripts/measure_calibration_ece.py](../../scripts/measure_calibration_ece.py)
**Module:** [backend/calibration.py](../../backend/calibration.py)

## What it measures

For each detector (SAM3, DOTA-OBB): bin predictions by confidence (e.g. 0-10%, 10-20%, …), measure the gap between mean confidence in the bin and empirical accuracy in the bin. Lower ECE = calibrated confidence; high ECE = over/underconfident.

## Output

- Markdown table with per-detector ECE pre/post temperature scaling.
- `model_temperatures.json` with optimal per-detector temperature values.

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
