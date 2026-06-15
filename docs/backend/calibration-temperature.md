# `backend/calibration.py` — Temperature Scaling

**Path:** [backend/calibration.py](../../backend/calibration.py)
**Lines:** ~165
**Depends on:** Env `MODEL_TEMPERATURES`, file `/data/calibration/model_temperatures.json` (mounted RO via the `calibration_data` named volume — see [How temperatures are shipped](#how-temperatures-are-shipped))

## Purpose

Single-parameter temperature scaling for detector confidence scores. SAM3, DOTA-OBB score on different distributions — uncalibrated, a 0.6 from one ≠ 0.6 from another. Rescales each model's logits into a common Platt-style probability space.

## Why this design

- **Single scalar per model** — trained offline on a held-out set; per-detector value in JSON file or env. No per-class temperatures (needs much more held-out data).
- **Identity transform when unconfigured** — missing JSON / empty env → raw score unchanged. Safe default.
- **Hot reload** — `reload_temperatures()` callable from inference router; operators adjust without restart.

## Key symbols

- [`_load_temperatures`](../../backend/calibration.py#L46) — env first, then file; returns `(temperatures, metadata)`.
- [`reload_temperatures`](../../backend/calibration.py#L107) — manual cache bust.
- [`temperature_for`](../../backend/calibration.py#L115) — `(model_tag) -> float`.
- [`calibrate_confidence`](../../backend/calibration.py#L132) — `(raw_score, model_tag) -> float`.
- [`status`](../../backend/calibration.py#L163) — `(model_count, models, measured_at, measured_against, source)`; exposed in `/api/inference/dashboard` under the `calibration` key.

## How to (re)fit

Run [scripts/measure_calibration_ece.py](../scripts/eval-runners.md) — writes `model_temperatures.json` with optimal per-model T from minimizing Expected Calibration Error on the eval set.

## How temperatures are shipped

The runtime file at `/data/calibration/model_temperatures.json` is **not** edited in place. Instead it ships as part of the `assets` image:

1. The host-checked-in `assets/static/calibration/model_temperatures.json` is baked into the assets image at `/opt/baked-calibration/`.
2. The assets entrypoint rsyncs the baked tree onto the `calibration_data` named volume on every container start (digest-gated by `MANIFEST.sha256`).
3. `backend` + `worker` mount that volume read-only at `/data/calibration/`.
4. This module reads the JSON on import (and `reload_temperatures()` allows hot bust without a restart).

Both shapes are accepted for back-compat:

- **Wrapped (canonical, as shipped):** `{ "temperatures": { "sam3": 0.8, ... }, "_measured_at": "2026-05-28", ... }`
- **Flat (legacy):** `{ "sam3": 0.8, "dota_obb": 1.1, ... }`

Keys beginning with `_` are skipped in the flat path so metadata fields don't pollute the lookup. When the file is missing the loader returns an empty dict and `temperature_for(...)` short-circuits to 1.0 (identity transform).

Full measurement → bake → rebuild procedure: [operations/calibration-shipping.md](../operations/calibration-shipping.md).

## Cross-references

- [scripts/eval-runners.md](../scripts/eval-runners.md)
- [benchmarks/calibration-ece.md](../benchmarks/calibration-ece.md)
- [operations/calibration-shipping.md](../operations/calibration-shipping.md)
