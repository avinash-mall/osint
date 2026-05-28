# `backend/detection_policy.py` — Precision Open-Vocab Filter Policy

**Path:** [backend/detection_policy.py](../../backend/detection_policy.py)
**Lines:** ~430
**Depends on:** [backend/ontology.py](../../backend/ontology.py), [backend/database.py](../../backend/database.py), env `GLOBAL_CONFIDENCE_FLOOR`, `PER_CLASS_CONFIDENCE_OVERRIDES`, `DETECTION_THRESHOLD_PROFILE`, `LABEL_VERIFIER_MARGIN_FLOOR`

## Purpose

Single policy module: should a raw `/detect` detection be emitted, what is its `parent_class`, which confidence floor applies? Backwards-compatible wrapper around [`ontology.normalize`](ontology-system.md).

## Why this design

- **Open-vocabulary, precision default** — every label first-class ([decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)). Default profile `defence_precision`, `GLOBAL_CONFIDENCE_FLOOR=0.40` (raised from 0.35 to cut false positives — see [decisions/why-deconflicted-detection-prompts.md](../decisions/why-deconflicted-detection-prompts.md)); operators lower the floor or use `PER_CLASS_CONFIDENCE_OVERRIDES`.
- **Per-class floor defaults** — `DEFAULT_PER_CLASS_THRESHOLDS` ships code-level floors for the **runtime canonical `parent_class` labels** (not the benchmark's collapsed bucket names) whose measured precision is unacceptable at the global default: every object under the `Transportation_Terrain` seed branch gets `0.55`, and the ontology fallback `unknown` gets `0.50`. Merged below env + DB so operator overrides always win. See [decisions/why-transportation-floor-raised.md](../decisions/why-transportation-floor-raised.md) for the bucket → runtime-label mapping and the trap that ate the first attempt.
- **`parent_class_for_label` = public bucket assignment** — used by imagery worker, FMV worker, UI category facets, graph type-matching queries. Falls back to the normalized label when no parent matches.
- **Policy cached** — `active_detection_policy()` reads `PER_CLASS_CONFIDENCE_OVERRIDES` from DB once, reuses; `invalidate_policy_cache()` called by inference router on overrides PUT.

## Key symbols

- [`DEFAULT_PER_CLASS_THRESHOLDS`](../../backend/detection_policy.py#L36-L78) — code-shipped per-parent floors, keyed by runtime canonical `parent_class` (the 10 objects under `Transportation_Terrain` plus the `unknown` fallback); env + DB overrides win.
- [`normalize_label`](../../backend/detection_policy.py#L86) — wraps `ontology._canonical`.
- [`strip_source_prefix`](../../backend/detection_policy.py#L93) — removes `"sam3:"`, `"yoloe:"`, etc.
- [`parent_class_for_label`](../../backend/detection_policy.py#L102) — **the** public function; clusters into broad open buckets.
- [`active_detection_policy`](../../backend/detection_policy.py#L208) — reads defaults + env + DB overrides → dict; precedence `defaults < env < DB`.
- [`invalidate_policy_cache`](../../backend/detection_policy.py#L230).
- [`threshold_for_parent`](../../backend/detection_policy.py#L236) — `class_thresholds[parent]` with global-floor fallback.
- [`detection_decision`](../../backend/detection_policy.py#L244) — `{emit: bool, reasons: list[str]}` per detection.
- [`should_emit_detection`](../../backend/detection_policy.py#L286) — boolean shortcut.
- [`DOTA_OBB_GENERIC_CLASSES`](../../backend/detection_policy.py#L312) — the 18 deliberately-generic DOTA-OBB v1 categories, pre-normalised. Task 1.2 anchor.
- [`label_quality_for`](../../backend/detection_policy.py#L339) — classifies a detection's display trust as `verified` / `inferred` / `generic`. See [decisions/why-generic-labels-when-unverified.md](../decisions/why-generic-labels-when-unverified.md).
- [`display_label_for`](../../backend/detection_policy.py#L374) — returns `(display_label, label_quality)`; suppresses fabricated specific labels on unverified DOTA-OBB generics.

## Inputs / Outputs

Input: raw model labels + confidence scores from imagery worker after calibration; per-class floors from `DEFAULT_PER_CLASS_THRESHOLDS` (code) ⊕ `PER_CLASS_CONFIDENCE_OVERRIDES` env JSON ⊕ `inference_config.config['per_class_confidence_overrides']` DB row (each layer overrides the previous). Output: policy record — normalized/original class, parent class, calibrated confidence, active class threshold, review status, profile, taxonomy version, model version.

For Task 1.2, the worker persistence path additionally calls `display_label_for(det, ont)` and stores two advisory metadata fields:

- `display_label: str` — the analyst-facing string. Generic DOTA-OBB rows
  surface as `"Aircraft (generic)"` / `"Vehicle (generic)"` instead of the
  ontology's tie-broken specific label.
- `label_quality: "verified" | "inferred" | "generic"` — drives the
  SelectionPanel chip and the MapStage popup's `LABEL_QUALITY` row.

These fields are **advisory only**: the `class` SQL column and every existing
metadata key (`original_class`, `parent_class`, `canonical_label`, …) remain
untouched for audit and future re-promotion.

## Failure modes

- DB `inference_config` overrides optional; unavailable → env defaults.
- Invalid JSON in `PER_CLASS_CONFIDENCE_OVERRIDES` ignored.
- Detections below active floor → marked `below_class_threshold`, skipped by worker.

## Cross-references

- [backend/ontology-system.md](ontology-system.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [decisions/why-transportation-floor-raised.md](../decisions/why-transportation-floor-raised.md)
- [decisions/why-generic-labels-when-unverified.md](../decisions/why-generic-labels-when-unverified.md)
- [backend-routers/inference-router.md](../backend-routers/inference-router.md) — PUT confidence overrides
- [frontend/admin-conf-overrides.md](../frontend/admin-conf-overrides.md)
- [inference/dota-obb-specialist.md](../inference/dota-obb-specialist.md) — the 18 generic categories
- [inference/remoteclip-verifier.md](../inference/remoteclip-verifier.md) — verifier that lifts `generic → verified`
