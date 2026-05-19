# Why Open-Vocabulary Classification

## Decision

There is **no closed taxonomy**. Every label SAM3 emits from explicit prompts, precision defaults, or opt-in ontology prompts is accepted as a first-class object class. Precision defaults decide which prompts run automatically; they do not create a closed label set.

## Why

- **Operational reality.** A real GEOINT deployment encounters objects the original taxonomy didn't anticipate: a new variant of an aircraft, a previously unseen civilian asset shape, an LLM-suggested label that's domain-meaningful. A closed taxonomy means "delete" — and silent data loss is worse than a slightly noisy detection list.
- **Operator triage workflow.** Unknown LLM-emitted labels go to `ontology_unknown_labels` table. Operators see them in the Admin → Ontology UI and either assign to an existing object or create a new one. See [operations/unknown-label-triage.md](../operations/unknown-label-triage.md).
- **Soft clustering via parent_class.** [`parent_class_for_label`](../../backend/ontology.py) groups detections into broad open buckets (aircraft, vessel, vehicle, train, building, infrastructure, storage_tank, bridge, harbor, airfield, recreation, vegetation, water, person, animal, food, furniture, household, electronic, tool, clothing, plant, sport, segment, track) for UI grouping — and **falls back to the normalized label itself** when no cluster matches. So new labels appear under their own name; they're not silently demoted.

## Implementation contract

- **Prompt resolution order** (each step short-circuits):
  1. `metadata.text_prompts` — explicit list per request; an explicit empty list is HTTP 400 unless box prompts are supplied
  2. Bounded precision defaults for the sensor mapped from `metadata.modality`
  3. Backend ontology defaults only when `SAM3_DEFAULT_PROMPT_SOURCE=ontology` or `backend`
- **All prompts** pass through: trim → lowercase → dedupe-preserve-order → cap at `SAM3_MAX_PROMPTS_PER_REQUEST` (default 64).
- **Confidence floors only.** `DETECTION_THRESHOLD_PROFILE=defence_precision` defaults to `GLOBAL_CONFIDENCE_FLOOR=0.35`; `PER_CLASS_CONFIDENCE_OVERRIDES={}` can lower or raise class-specific floors. Do not delete labels from ontology merely to suppress noise, because that also excludes them from the unknown-label workflow.

## Trade-offs accepted

- More post-processing work to compress and triage the long tail.
- Bench numbers must be reported per-class, not globally, since the class set is unbounded.

## Cross-references

- [backend/detection-policy.md](../backend/detection-policy.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [operations/unknown-label-triage.md](../operations/unknown-label-triage.md)
- [why-category-presence-gate.md](why-category-presence-gate.md) — how absent-concept hallucinations are still suppressed
- [why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md)
