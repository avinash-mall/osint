# Calibration temperatures (per-detector)

`model_temperatures.json` ships from the host into the `assets` docker image
via `assets/Dockerfile` and is rsync'd onto the `calibration_data` named
volume on container start. The backend + worker mount that volume read-only
at `/data/calibration/` and `backend/calibration.py` picks the file up
automatically (file absent => identity transform, safe default).

Defaults are T=1.0 for every detector (identity). To ship measured
temperatures, run `python scripts/measure_calibration_ece.py`, copy the
suggested values into the `temperatures` block of `model_temperatures.json`,
regenerate `MANIFEST.sha256` (`sha256sum model_temperatures.json | cut -d' ' -f1`),
then rebuild the `assets` service. The full procedure lives in
[../../../docs/operations/calibration-shipping.md](../../../docs/operations/calibration-shipping.md).
