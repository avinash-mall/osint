# `backend/calibration.py` — Temperature Scaling

**Path:** [backend/calibration.py](../../backend/calibration.py)
**Lines:** ~153
**Depends on:** Env `MODEL_TEMPERATURES`, file `/data/calibration/model_temperatures.json`

## Purpose

Single-parameter temperature scaling for detector confidence scores. SAM3, DOTA-OBB, and Grounding-DINO produce scores on different distributions — without calibration, a 0.6 from one is not comparable to a 0.6 from another. Temperature scaling rescales each model's logits to a common Platt-style probability space.

## Why this design

- **Single scalar per model.** Trained offline on a held-out set; per-detector value lives in a JSON file or env var. No per-class temperatures (would need much more held-out data to fit).
- **Identity transform when unconfigured.** Missing JSON file or empty env → returns the raw score unchanged. Safe default.
- **Hot reload.** `reload_temperatures()` is callable from the inference router so operators can adjust without restart.

## Key symbols

- [`_load_temperatures`](../../backend/calibration.py#L46) — env first, then file.
- [`reload_temperatures`](../../backend/calibration.py#L92) — manual cache bust.
- [`temperature_for`](../../backend/calibration.py#L100) — `(model_tag) -> float`.
- [`calibrate_confidence`](../../backend/calibration.py#L117) — `(raw_score, model_tag) -> float`.
- [`status`](../../backend/calibration.py#L148) — exposed in `/api/inference/dashboard`.

## How to (re)fit

Run [scripts/measure_calibration_ece.py](../scripts/eval-runners.md). It writes `model_temperatures.json` with optimal per-model T values from minimizing Expected Calibration Error on the eval set.

## Cross-references

- [scripts/eval-runners.md](../scripts/eval-runners.md)
- [benchmarks/calibration-ece.md](../benchmarks/calibration-ece.md)
