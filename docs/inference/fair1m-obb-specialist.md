# `inference-sam3/fair1m_obb.py` — FAIR1M-2.0 OBB Specialist

**Path:** [inference-sam3/fair1m_obb.py](../../inference-sam3/fair1m_obb.py)
**Lines:** ~215
**Depends on:** `ultralytics`, env `FAIR1M_OBB_MODEL_ID`, `FAIR1M_OBB_THRESHOLD`, `FAIR1M_OBB_IOU`, `FAIR1M_OBB_IMGSZ`, `FAIR1M_OBB_WEIGHTS_DIR`

## Purpose

Fine-grained closed-vocabulary oriented-bbox detector for the 37 FAIR1M-2.0
sub-classes (Boeing 737/747/777/787, A220/A321/A330/A350, Warship, Tugboat,
Dump Truck, Tractor, ...). Emits SAM3-shaped candidates so the shared
fusion path can combine FAIR1M sub-class detections with DOTA-OBB, SAM3,
and GDINO masks. Sibling of [dota_obb.py](../../inference-sam3/dota_obb.py)
with the same interface contract.

## Why this design

DOTA-v1's 18 generic classes collapse aircraft into "plane" and ships
into "ship" — useless for defence analyst questions like "Boeing 737 or
Su-25?". FAIR1M-2.0 is the canonical open benchmark for fine-grained
aerial OBB; its 37 sub-classes match the buckets the defence ontology
already exposes but where DOTA-v1 reports AP=0.0.

The runner is **load-flag default-on** but the checkpoint is **operator-baked**
(see [../operations/fair1m-bake.md](../operations/fair1m-bake.md)). When
the weights file is missing, `load()` returns `model=None` with an `error`
string; the dispatch loop in [main.py](main-app-entrypoint.md) checks
`bundle.get("fair1m_obb")` before dispatch so the layer simply contributes
zero candidates until weights land. Same graceful-no-op pattern as DOTA-OBB.

The specialist is **gated** by [fair1m_gate.py](../../inference-sam3/fair1m_gate.py)
so it only fires when prompts touch FAIR1M sub-class vocabulary the DOTA-v1
head does not already cover. Operator override via `metadata.force_fair1m_obb=true`.
See [../decisions/why-fair1m-specialist.md](../decisions/why-fair1m-specialist.md).

## Key symbols

- [`FAIR1M_CLASSES`](../../inference-sam3/fair1m_obb.py#L33-L52) — 37 fine-grained class names; asserted length=37.
- [`load`](../../inference-sam3/fair1m_obb.py#L82-L130) — resolves the weights path under `FAIR1M_OBB_WEIGHTS_DIR`, returns `{model: None, error: ...}` when missing — never raises.
- [`run`](../../inference-sam3/fair1m_obb.py#L133-L188) — runs one chip → `(mask, bbox_xyxy, score, label)` tuples; tensor `.float().cpu().numpy()` mirrors the DOTA-OBB pattern.
- [`_polygon_mask`](../../inference-sam3/fair1m_obb.py#L191-L205) — OBB corners → boolean polygon mask (cv2.fillPoly fallback to axis-aligned bbox).
- [`model_versions`](../../inference-sam3/fair1m_obb.py#L208-L218) — reports `loaded`, `model_id`, `weights_path`, `class_count=37`, `error`; surfaced in `/health.model_versions.fair1m_obb`.

## Inputs / Outputs

**Input:** RGB uint8 chip (any size — Ultralytics resizes to `FAIR1M_OBB_IMGSZ` internally).

**Output:** `list[(mask, bbox_xyxy, score, label)]` tuples; `main.py` tags each with `source_layer="fair1m_obb"` before fusion. Labels come straight from the checkpoint's `names` dict — they preserve FAIR1M spelling (e.g. "Boeing 737") so `classifyToBranch` and the ontology layer can pattern-match exactly.

## Failure modes

| Condition | Behaviour |
|---|---|
| Weights file missing | `load()` returns `{model: None, error: "weights file not found: ..."}`; runner returns `[]` on every call. Dispatch loop short-circuits via `bundle.get("fair1m_obb")` truthiness check. |
| Ultralytics not installed | `load()` returns `{model: None, error: "<ImportError>"}`. Same no-op runtime path. |
| `YOLO()` raises | Caught; returns `{model: None, error: str(exc)}`. |
| Inference OOM | `safe_predict` retries with `cuda_cleanup`; fallback returns `[]`. |
| OBB tensor conversion error | Logged, skips that result item. |

The specialist **never raises into the dispatch loop**. Operators can verify weights landed via `GET /health.model_versions.fair1m_obb.loaded`.

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md) — dispatch site (search for `fair1m_obb`)
- [dota-obb-specialist.md](dota-obb-specialist.md) — sibling pattern
- [fusion-and-nms.md](fusion-and-nms.md)
- [../decisions/why-fair1m-specialist.md](../decisions/why-fair1m-specialist.md)
- [../operations/fair1m-bake.md](../operations/fair1m-bake.md)
- [../conventions/adding-a-new-detection-model.md](../conventions/adding-a-new-detection-model.md)
