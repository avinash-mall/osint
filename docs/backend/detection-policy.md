# `backend/detection_policy.py` — Precision Open-Vocab Filter Policy

**Path:** [backend/detection_policy.py](../../backend/detection_policy.py)
**Lines:** ~245
**Depends on:** [backend/ontology.py](../../backend/ontology.py), [backend/database.py](../../backend/database.py), env `GLOBAL_CONFIDENCE_FLOOR`, `PER_CLASS_CONFIDENCE_OVERRIDES`, `DETECTION_THRESHOLD_PROFILE`

## Purpose

The single policy module: should a raw detection from `/detect` be emitted, what is its `parent_class`, and which confidence floor applies? Backwards-compatible wrapper around [`ontology.normalize`](ontology-system.md).

## Why this design

- **Open-vocabulary, precision default.** Every label remains first-class — see [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md). The default profile is `defence_precision` with `GLOBAL_CONFIDENCE_FLOOR=0.35`; operators can lower the floor or use `PER_CLASS_CONFIDENCE_OVERRIDES`.
- **`parent_class_for_label` is the public bucket assignment.** Used by the imagery worker, the FMV worker, the UI's category facets, and graph queries that want broad type matching. Falls back to the normalized label itself when no parent matches.
- **Policy is cached.** `active_detection_policy()` reads `PER_CLASS_CONFIDENCE_OVERRIDES` from DB once and reuses; `invalidate_policy_cache()` is called by the inference router when overrides are PUT.

## Key symbols

- [`normalize_label`](../../backend/detection_policy.py#L44) — trivial wrapper that calls `ontology._canonical`.
- [`strip_source_prefix`](../../backend/detection_policy.py#L51) — removes `"sam3:"`, `"yoloe:"`, etc.
- [`parent_class_for_label`](../../backend/detection_policy.py#L60) — **the** public function; clusters into broad open buckets.
- [`active_detection_policy`](../../backend/detection_policy.py#L164) — reads env + DB overrides, returns a dict.
- [`invalidate_policy_cache`](../../backend/detection_policy.py#L184).
- [`detection_decision`](../../backend/detection_policy.py#L198) — `{emit: bool, reasons: list[str]}` for a single detection.
- [`should_emit_detection`](../../backend/detection_policy.py#L240) — boolean shortcut.

## Inputs / Outputs

Inputs are raw model labels and confidence scores from the imagery worker after calibration. Output is a policy record with normalized/original class, parent class, calibrated confidence, active class threshold, review status, profile, taxonomy version, and model version.

## Failure modes

- DB-backed `inference_config` overrides are optional; when unavailable, env defaults are used.
- Invalid JSON in `PER_CLASS_CONFIDENCE_OVERRIDES` is ignored.
- Detections below the active floor are marked `below_class_threshold` and skipped by the worker.

## Cross-references

- [backend/ontology-system.md](ontology-system.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [backend-routers/inference-router.md](../backend-routers/inference-router.md) — PUT confidence overrides
- [frontend/admin-conf-overrides.md](../frontend/admin-conf-overrides.md)
