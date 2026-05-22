# Operations — Unknown Label Triage

## What is an "unknown" label?

A label that arrived from somewhere (LLM, SAM3 open-vocab output, manual operator entry) and didn't match any object in the current ontology. The platform writes it to `ontology_unknown_labels` rather than discarding — see [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md).

## How they accumulate

- **LLM classification** — when `ENABLE_LLM_DETECTION_CLASSIFICATION=true`, the post-classifier sometimes coins terms outside the ontology → logged.
- **SAM3 text output** — open-vocab labels SAM3 returns that don't match a normalized object.
- **Operator manual detection** — operator-typed classes on a manually drawn detection get triaged here.

## How to triage

1. **Admin → Ontology → Unknown labels tab.**
2. Each row shows: label string, occurrence count, latest timestamp, originating layer.
3. For each, operator picks one of:
   - **Assign to existing object** — maps the label to an existing `ontology_objects` row → future detections normalize there.
   - **Create new object** — creates a new object (with branch, default prompts per sensor, icon), assigns the label to it.
   - **Discard** — mark ignored (won't show again).

Backend: `POST /api/ontology/unknown-labels/{label}/assign` with `{object_id?: int, create?: {...}}` body.

## Why the operator drives this

Auto-assignment would let the ontology drift uncontrollably. An LLM that hallucinates "submarine" five times shouldn't auto-create a Submarine object — the operator decides.

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [frontend/ontology-admin-ui.md](../frontend/ontology-admin-ui.md)
