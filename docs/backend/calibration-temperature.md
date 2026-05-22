# `backend/calibration.py` — Temperature Scaling

**Path:** [backend/calibration.py](../../backend/calibration.py)
**Lines:** ~153
**Depends on:** Env `MODEL_TEMPERATURES`, file `/data/calibration/model_temperatures.json`

## Purpose

Single-parameter temperature scaling for detector confidence scores. SAM3, DOTA-OBB, Grounding-DINO score on different distributions — uncalibrated, a 0.6 from one ≠ 0.6 from another. Rescales each model's logits into a common Platt-style probability space.

## Why this design

- **Single scalar per model** — trained offline on a held-out set; per-detector value in JSON file or env. No per-class temperatures (needs much more held-out data).
- **Identity transform when unconfigured** — missing JSON / empty env → raw score unchanged. Safe default.
- **Hot reload** — `reload_temperatures()` callable from inference router; operators adjust without restart.

## Key symbols

- [`_load_temperatures`](../../backend/calibration.py#L46) — env first, then file.
- [`reload_temperatures`](../../backend/calibration.py#L92) — manual cache bust.
- [`temperature_for`](../../backend/calibration.py#L100) — `(model_tag) -> float`.
- [`calibrate_confidence`](../../backend/calibration.py#L117) — `(raw_score, model_tag) -> float`.
- [`status`](../../backend/calibration.py#L148) — exposed in `/api/inference/dashboard`.

## How to (re)fit

Run [scripts/measure_calibration_ece.py](../scripts/eval-runners.md) — writes `model_temperatures.json` with optimal per-model T from minimizing Expected Calibration Error on the eval set.

## Cross-references

- [scripts/eval-runners.md](../scripts/eval-runners.md)
- [benchmarks/calibration-ece.md](../benchmarks/calibration-ece.md)
