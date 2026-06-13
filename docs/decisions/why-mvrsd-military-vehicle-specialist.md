# Why a default-ON MVRSD military-vehicle specialist

**Decision date:** 2026-06-12
**Status:** ACTIVE

## Context

The default detection stack (SAM 3 open-vocab + DOTA-OBB) resolves vehicles only as coarse `large vehicle` / `small vehicle`. Defence analysts working sub-meter (~0.3 m GSD) optical RGB need a finer military taxonomy: Small Military Vehicle (SMV), Large Military Vehicle (LMV), Armored Fighting Vehicle (AFV), Cargo Vehicle (CV), Military Combat Vehicle (MCV). The Military Vehicle Remote Sensing Dataset (MVRSD) provides exactly these five labels, so we fine-tuned `yolo11m` (Ultralytics detect, axis-aligned boxes) on it.

## Decision

Add MVRSD as a **default-ON** specialist detector — modelled on and treated exactly like [dota_obb.py](../../inference-sam3/dota_obb.py).

- **Default ON, tied to the optional-models master switch.** `SAM3_LOAD_MVRSD` defaults to `_DEFAULT` (= `"1"` when `SAM3_LOAD_OPTIONAL_MODELS=1`), exactly like DOTA-OBB and DINOv3-SAT. It loads with the `imagery_rgb` profile and respects low-VRAM (16 GiB) profiles through the same master switch — it is off only if `SAM3_LOAD_OPTIONAL_MODELS=0` or `SAM3_LOAD_MVRSD=0`.
- **Runs on every RGB `/detect`** via the normal default-True `_layer_active("mvrsd")` filter — exactly like DOTA-OBB, not the old explicit-opt-in `"mvrsd" in _enabled` path. An unfiltered request triggers it; when a request supplies a non-empty `enabled_layers` list it runs only if `mvrsd` is included (same rule as every other layer). The frontend `optical` sensor profile lists `mvrsd` in its `enabledLayers`.
- **`imagery_rgb` only** (it is RGB-specific; no MSI/SAR/FMV value).
- **Standard fusion.** Its HBB detections feed the same WBF/NMS path and policy floor as every other layer, with trust weight `1.0` (DOTA-OBB parity) in `fusion._DEFAULT_WBF_WEIGHTS`.
- **Tradeoff accepted + mitigated.** A fine-grained military classifier can assign military sub-types to civilian vehicles on arbitrary imagery. This is now accepted and mitigated by: the confidence-policy floor (`GLOBAL_CONFIDENCE_FLOOR` + `MVRSD_CONF`), RGB-only scoping (it loads only in the optical/RGB profile, never MSI/SAR), and a per-request opt-out (exclude it from `enabled_layers`, or `SAM3_LOAD_MVRSD=0`). Unlike [removed-defence-yolo.md](removed-defence-yolo.md) (1297 FPs / 0 TPs), MVRSD is a *trained* fine-grained detector whose output is subject to the precision-policy floor, not an indiscriminate one — so it earns its place in the default RGB stack.

## Weights / offline

Trained weights are hosted as a **GitHub release asset** (the project ships no MVRSD checkpoint in-repo). Per hard rule #8 (offline at runtime, no runtime downloads), the weight is fetched at **build time**: the orchestrator sets the build ARG `MVRSD_WEIGHTS_URL` to the release-asset URL, and `Dockerfile.gpu` `curl`s it to `/models/mvrsd/mvrsd_yolo11m.pt`. When the ARG is empty the bake step is a no-op and `mvrsd.load()` honour-gates (returns `model=None`), so a build without the weight degrades cleanly — the layer simply contributes zero candidates. Runtime path overridable via `MVRSD_WEIGHTS_PATH`.

## Consequences

- One more default detector in the RGB stack, tied to the optional-models master switch; a build without the baked weight degrades cleanly (skip-if-empty bake → honour-gate).
- Analysts get a fine military taxonomy on RGB scenes out of the box, on the imagery it was trained for; operators who do not want it opt out per request or set `SAM3_LOAD_MVRSD=0`.
- Validated against the recipe's benchmark gate before enabling by default ([adding-a-new-detection-model.md](../conventions/adding-a-new-detection-model.md)); the confidence-policy floor keeps its precision in line with the rest of the stack.

## Cross-references

- [inference/mvrsd-specialist.md](../inference/mvrsd-specialist.md)
- [inference/dota-obb-specialist.md](../inference/dota-obb-specialist.md)
- [decisions/removed-defence-yolo.md](removed-defence-yolo.md)
- [conventions/adding-a-new-detection-model.md](../conventions/adding-a-new-detection-model.md)
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
