# Why Precision-First Inference Defaults

## Decision

Keep open-vocabulary labels, but make the default inference path precision-first:

- `/detect` uses explicit `metadata.text_prompts` when supplied.
- Explicit empty `metadata.text_prompts: []` is a 400 unless box prompts are supplied.
- Omitted `/detect` prompts use bounded per-sensor defaults instead of the full backend ontology prompt list.
- Full ontology fan-out remains available with `SAM3_DEFAULT_PROMPT_SOURCE=ontology` or `backend`.
- DOTA-OBB and Grounding-DINO are relevance/intent gated; request metadata can force them for experiments.
- The backend policy default is `DETECTION_THRESHOLD_PROFILE=defence_precision` with `GLOBAL_CONFIDENCE_FLOOR=0.35`.
- FMV PCS requests without user prompts use `vehicle,person,building`; YOLOE prompt-free tracking is unchanged.

## Why

False positives and slow inference were traced to four connected issues:

- `/detect` fanned out broad ontology prompt sets when the caller omitted prompts.
- The open-vocabulary policy accepted low-confidence detections by default.
- Specialist layers could add detections unrelated to the analyst's requested concept.
- Detections did not consistently carry `source_layer`, so backend calibration could not use detector-specific provenance.

The new default favors usable analyst review over broad recall. Rare or novel targets remain supported through explicit prompts, per-request force flags, or ontology fan-out mode.

## Implementation Contract

- Prompt resolution is implemented in [inference-sam3/main.py](../../inference-sam3/main.py): explicit prompts are normalized and deduped; omitted prompts use `_precision_default_prompts`; empty explicit prompts fail fast.
- Detector provenance is carried as `source_layer` before fusion and response serialization.
- `/detect` debug output includes `prompt_count`, `candidates_by_layer`, `suppressed_by_nms`, and `suppressed_by_policy`.
- Backend calibration uses `source_layer` through `_calibration_tag_for_detection`.
- FMV fallback prompts are controlled by `FMV_DEFAULT_PROMPTS`.

## Trade-offs Accepted

- Some rare targets require explicit prompts or ontology fan-out mode.
- Grounding-DINO no longer runs just because it is loaded; callers must enable or force it when they want uncommon-prompt coverage.
- Default persisted detections are fewer, but the retained set is easier to review and calibrate.

## Cross-references

- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md)
- [backend/detection-policy.md](../backend/detection-policy.md)
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [inference/dota-obb-specialist.md](../inference/dota-obb-specialist.md)
- [inference/grounding-dino-detector.md](../inference/grounding-dino-detector.md)
- [why-open-vocabulary.md](why-open-vocabulary.md)
