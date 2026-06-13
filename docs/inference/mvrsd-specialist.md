# `inference-sam3/mvrsd.py` — MVRSD Military-Vehicle Specialist

**Path:** [inference-sam3/mvrsd.py](../../inference-sam3/mvrsd.py)
**Lines:** ~185
**Depends on:** `ultralytics`, `inference_utils` (`safe_predict`, `cuda_cleanup`, `device_ctx`, `apply_yolo_optimizations`), env `SAM3_LOAD_MVRSD`, `MVRSD_WEIGHTS_PATH`, `MVRSD_CONF`, `MVRSD_IOU`, `MVRSD_IMGSZ`, build ARG `MVRSD_WEIGHTS_URL`

## Purpose

Closed-vocabulary, axis-aligned (HBB) YOLO **detect** specialist for sub-meter (~0.3 m GSD) optical-RGB military vehicles, fine-tuned from `yolo11m` on the Military Vehicle Remote Sensing Dataset (MVRSD). Five fixed classes: `0=SMV`, `1=LMV`, `2=AFV`, `3=CV`, `4=MCV`. Emits SAM3-shaped candidates `(mask, bbox_xyxy, score, label)` so the shared [fusion.py](../../inference-sam3/fusion.py) path combines its detections with SAM3 / DOTA-OBB / Grounding-DINO.

## Why this design

It ships **default-ON** in the `imagery_rgb` profile, treated exactly like DOTA-OBB: `SAM3_LOAD_MVRSD` defaults to `_DEFAULT` (= `"1"` when `SAM3_LOAD_OPTIONAL_MODELS=1`), and once loaded it runs on every RGB `/detect` via the normal default-True `_layer_active("mvrsd")` filter (an unfiltered request triggers it; a non-empty `enabled_layers` runs it only if `mvrsd` is included). The known tradeoff — a fine-grained military classifier can assign military sub-types to civilian vehicles on arbitrary scenes — is accepted and gated by the confidence policy (`GLOBAL_CONFIDENCE_FLOOR` + `MVRSD_CONF`), RGB-only scoping, and per-request opt-out. It mirrors `dota_obb.py` exactly (fp32 forced, `device_ctx` pinning, `safe_predict` OOM retry) — the only structural difference is the detect head returns `boxes.xyxy` instead of an OBB polygon, so the mask is a filled axis-aligned rectangle and the downstream OBB record uses `fusion._hbb_fallback`. See [decisions/why-mvrsd-military-vehicle-specialist.md](../decisions/why-mvrsd-military-vehicle-specialist.md).

## Key symbols

- [`load`](../../inference-sam3/mvrsd.py#L56-L89) — loads the fine-tuned checkpoint from `MVRSD_WEIGHTS_PATH` (default `/models/mvrsd/mvrsd_yolo11m.pt`); honour-gates to `model=None` if the file is absent (empty `MVRSD_WEIGHTS_URL` at build → skipped bake).
- [`run`](../../inference-sam3/mvrsd.py#L91-L157) — runs one RGB chip → `(mask, bbox_xyxy, score, label)` tuples, applying `MVRSD_CONF` floor.
- [`_box_mask`](../../inference-sam3/mvrsd.py#L159-L170) — axis-aligned box → filled boolean mask matching the chip size.
- [`model_versions`](../../inference-sam3/mvrsd.py#L173-L183) — reports loaded model id, threshold, image size, class list, error. Surfaced in `/health` `model_versions.mvrsd`.

## Inputs / Outputs

Input: RGB uint8 chip + the per-request `enabled_layers` (default-True via `_layer_active`, like DOTA-OBB). Output candidates are tagged by `main.py` with `source_layer="mvrsd"` before WBF/NMS fusion, verifier scoring, backend calibration, and evidence ranking — identical to every other detector. WBF trust weight `1.0` (parity with DOTA-OBB) in `fusion._DEFAULT_WBF_WEIGHTS`.

## Failure modes

Missing weight file (empty build URL) / missing Ultralytics → unloaded bundle; layer contributes zero candidates and `/health` shows `loaded: false`. Inference errors are logged and return an empty list for that chip. fp16/bf16 dtype mismatch is avoided by forcing fp32 (`MVRSD_HALF=False`).

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md) — loader dispatch, `_HEALTH_COMPONENT_SLUGS`, `/detect` flow, `enabled_layers`
- [profile-pool-lifecycle.md](profile-pool-lifecycle.md) — default `imagery_rgb` member
- [fusion-and-nms.md](fusion-and-nms.md) — WBF weight + HBB fusion
- [model-manifest.md](model-manifest.md) — weight entry + bake
- [dota-obb-specialist.md](dota-obb-specialist.md) — the analog this mirrors
- [decisions/why-mvrsd-military-vehicle-specialist.md](../decisions/why-mvrsd-military-vehicle-specialist.md)
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
