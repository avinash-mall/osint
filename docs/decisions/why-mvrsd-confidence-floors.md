# Why MVRSD got per-class confidence floors (and its classes were registered in the ontology)

**Date:** 2026-06-14
**Affects:** [backend/scripts/seeds/defenceOntology.seed.json](../../backend/scripts/seeds/defenceOntology.seed.json), `ontology_branches`/`ontology_objects` (live DB), `inference_config` (live DB, per-class floors via [backend/routers/inference.py](../../backend/routers/inference.py) `PUT /api/inference/confidence-overrides`), [backend/detection_policy.py](../../backend/detection_policy.py) (the parent_class key mapping)

## Problem

MVRSD ([inference/mvrsd-specialist.md](../inference/mvrsd-specialist.md)) was validated firing end-to-end on real sub-meter RGB, but two issues surfaced:

1. **Its 5 classes weren't in the ontology.** `SMV/LMV/AFV/CV/MCV` had no `ontology_objects` row, so [`ontology.normalize`](../backend/ontology-system.md) fell through to the `Other`/`unknown` branch, logged each to the `ontology_unknown_labels` triage queue, and emitted throttled `unknown label='lmv' layer='mvrsd' -> Other` warnings. MVRSD detections kept their raw `class` but got no military grouping/display.
2. **It over-fires military sub-types on civilian vehicles** — the tradeoff [why-mvrsd-military-vehicle-specialist.md](why-mvrsd-military-vehicle-specialist.md) accepted as "mitigated by the confidence-policy floor." That mitigation claim had never been measured.

## Ontology registration (the prerequisite for the floors)

Added a branch `Military_Vehicles_MVRSD` under `Military_Forces` with 5 objects, applied to **both** the seed JSON (bootstrap source of truth) and the **live DB** via the admin API (`POST /api/ontology/branches` + `/objects` — non-destructive, bumps `ontology_version` so the separate worker process refreshes its `normalize` cache within ~2 s, no restart). Followed [conventions/adding-a-new-ontology-object.md](../conventions/adding-a-new-ontology-object.md).

**Load-bearing detail — object `label` = the short code, `prompt` = the long form** (`MVRSD_SMV` → label `SMV`, prompt `small military vehicle`, …). This is the same runtime-key-mapping trap documented in [why-transportation-floor-raised.md](why-transportation-floor-raised.md): [`ontology.normalize`](../../backend/ontology.py) derives `parent_class` from `_canonical(object.label)`, and `parent_class` is the key the per-class floors use in [detection_policy.py](../../backend/detection_policy.py). With short-code labels, `normalize('SMV')` → `parent_class='smv'`, so a floor keyed `"smv"` actually fires. Had the label been the long form, `parent_class` would be `small_military_vehicle` and a `"smv"`-keyed floor would silently no-op. Verified in both backend and worker: `normalize('SMV') → branch=Military_Vehicles_MVRSD, parent_class=smv, was_unknown=False`.

Registration does **not** pollute SAM3's prompt set: `SAM3_DEFAULT_PROMPT_SOURCE` defaults to `precision` (bounded built-ins), so the new prompts only reach SAM3 if an operator flips it to `ontology` (see [main-app-entrypoint.md](../inference/main-app-entrypoint.md)).

## Evaluation — what drove the floors

No ground-truth labels exist, so TP/FP was judged by **civilian-vs-military contrast + visual overlay**:

- **Civilian control** — Austin (0.5 m): no military vehicles, so every MVRSD detection is a false positive by construction. Gives the FP confidence distribution.
- **Military test** — Al Udeid Air Base + Grafenwoehr (z19 ≈ 0.3 m, fetched with [scripts/download_city_cog.py](../../scripts/download_city_cog.py) `--source esri`). Gives the TP-bearing distribution.

Per-layer candidate counts (`inference_summary.candidates_by_layer.mvrsd`): Austin 75, Al Udeid 507, Grafenwoehr 7.

**Visual check (decisive):** the top Al Udeid MVRSD detections land on real vehicles (vans, trucks, parking rows) — MVRSD has genuine signal.

**Core finding — confidence does NOT separate military from civilian:**

| class | civilian FP ceiling (Austin) | military max (Al Udeid) | military count |
| ----- | ---------------------------: | ----------------------: | -------------: |
| smv   | 0.447 | 0.626 | 142 |
| lmv   | 0.510 | 0.624 |  97 |
| afv   | 0.583 (0.754 on a re-run) | 0.642 |  11 |
| cv    | 0.580 | 0.550 |  40 |
| mcv   | 0.444 | 0.558 |  40 |

Only 3 of 330 Al Udeid detections clear 0.60; the bulk sit at 0.1–0.5, the same band as civilian FPs. A civilian car in Austin scored `afv 0.754` (MVRSD-only, `raw==calibrated`). **Root cause:** MVRSD's confidence encodes "is this a vehicle," not "is this military" — it labels every vehicle a military subtype, so a civilian car and a base truck score alike.

## Decision

Apply **per-class confidence floors** to the MVRSD classes via the runtime DB endpoint (`inference_config.config['per_class_confidence_overrides']`), keyed by the `parent_class` short codes. Precedence is `code defaults < PER_CLASS_CONFIDENCE_OVERRIDES env < inference_config DB` (see [detection-policy.md](../backend/detection-policy.md)); these are the **DB layer**.

Measured tradeoff (Austin civilian FPs vs Al Udeid military retained):

| floor setting | civilian FPs | military kept / 330 |
| ------------- | -----------: | ------------------: |
| none (0.0, deployment default) | 48 | 330 |
| strict (per-class civilian ceiling: smv 0.45, lmv 0.52, afv 0.59, cv 0.58, mcv 0.45) | 6 | 35 |
| **gentle uniform 0.40 (applied)** | **17** | **86** |

Chose the **gentle 0.40** floor: it trims the 0.1–0.3 noise band (`suppressed_by_policy` 1 → 47 on Austin), cuts civilian FPs 65% (48 → 17), and keeps 86 of the higher-confidence military detections (2.5× the strict floor's recall). It is scoped to the five MVRSD classes — other detectors keep the deployment's `GLOBAL_CONFIDENCE_FLOOR=0.0`.

These floors are **DB-only runtime state**, not shipped `DEFAULT_PER_CLASS_THRESHOLDS` code defaults (unlike transportation), because they are imagery/eval-specific and expected to be tuned. Promote them into [detection_policy.py](../../backend/detection_policy.py) `DEFAULT_PER_CLASS_THRESHOLDS` if they prove stable across more scenes.

## Why floors are only a partial fix

Floors **trim**; they cannot **separate**. Because the civilian and military confidence distributions overlap (civilian FPs reach 0.58, occasionally 0.75; military TPs concentrate ≤0.64), no floor zeroes civilian FPs without gutting military recall. This nuances the "mitigated by the confidence-policy floor" line in [why-mvrsd-military-vehicle-specialist.md](why-mvrsd-military-vehicle-specialist.md): the floor reduces FP volume but does not make MVRSD precise.

## The deeper fix (deliberately NOT done here)

- **AOI / scene gating** — enable MVRSD only on confirmed-military AOIs. This is the genuinely correct fix and validates the *original* `default-OFF` intent the module carried before it was made default-on; no per-AOI layer gate exists today (would be new work).
- **Retrain with civilian hard-negatives** — teach MVRSD that civilian cars/trucks are negatives. The model-side fix.
- **Keep MVRSD strictly `review_candidate`** — already the deployment's behavior (`GLOBAL_CONFIDENCE_FLOOR=0.0`, every MVRSD detection lands in analyst triage, never auto-confirmed).
- **No class deletion** — open-vocab/hard-rule preserved ([why-open-vocabulary.md](why-open-vocabulary.md)). MVRSD, unlike [removed-defence-yolo.md](removed-defence-yolo.md) (0 TP / 1297 FP), produces real TPs, so flooring — not removal — is correct.

## Reproduce

`scripts/download_city_cog.py "<base>" --source esri --zoom 19 --radius-km 1.5` → `POST /api/ingest/upload` (auth, `auto_process=true`) → query `detections.metadata->>'source_layer'='mvrsd'` for `original_class` / `calibrated_confidence` / `branch_id`. Tune the floors with `PUT /api/inference/confidence-overrides` (empty `per_class_confidence_overrides` clears them).

## Cross-references

- [decisions/why-mvrsd-military-vehicle-specialist.md](why-mvrsd-military-vehicle-specialist.md) — the detector + the accepted tradeoff this measures
- [decisions/why-transportation-floor-raised.md](why-transportation-floor-raised.md) — the per-class-floor precedent + the parent_class key trap
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md), [decisions/why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md)
- [backend/detection-policy.md](../backend/detection-policy.md) — floor layering + `threshold_for_parent`
- [backend-routers/inference-router.md](../backend-routers/inference-router.md) — `GET/PUT /api/inference/confidence-overrides`
- [inference/mvrsd-specialist.md](../inference/mvrsd-specialist.md), [conventions/adding-a-new-ontology-object.md](../conventions/adding-a-new-ontology-object.md)
