# Operations — Unknown Label Triage

## What is an "unknown" label?

A label that arrived from somewhere (LLM, SAM3 open-vocab output, manual operator entry) and didn't match any object in the current ontology. The platform writes it to `ontology_unknown_labels` rather than discarding — see [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md).

## How they accumulate

- **LLM classification.** When `ENABLE_LLM_DETECTION_CLASSIFICATION=true`, the post-classifier sometimes coins terms outside the ontology. Those get logged.
- **SAM3 text output.** Open-vocab labels SAM3 returns that don't match a normalized object.
- **Operator manual detection.** When the operator types a class on a manually drawn detection, unknown classes get triaged here.

## How to triage

1. **Admin → Ontology → Unknown labels tab.**
2. Each row shows: the label string, occurrence count, latest timestamp, originating layer.
3. For each, the operator picks one of:
   - **Assign to existing object.** Maps the label to an existing `ontology_objects` row so future detections normalize there.
   - **Create new object.** Creates a new object (with branch, default prompts per sensor, icon) and assigns the label to it.
   - **Discard.** Mark as ignored (won't show again).

Backend: `POST /api/ontology/unknown-labels/{label}/assign` with `{object_id?: int, create?: {...}}` body.

## Why the operator drives this

Auto-assignment would let the ontology drift uncontrollably. An LLM that hallucinates "submarine" five times shouldn't auto-create a Submarine object — the operator decides.

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [frontend/ontology-admin-ui.md](../frontend/ontology-admin-ui.md)
