# Admin — Confidence Overrides

**Path:** [frontend/src/components/admin/ConfOverrideView.tsx](../../frontend/src/components/admin/ConfOverrideView.tsx)
**Lines:** ~10952 characters

## Purpose

Per-class confidence floor editor. Surfaces the `PER_CLASS_CONFIDENCE_OVERRIDES` JSON map as a table, each row `{class, floor}`. Edits persist to PostGIS, apply on the next request to inference.

## Why this lives in the UI

`PER_CLASS_CONFIDENCE_OVERRIDES` was originally env-only → a restart for any threshold change. Source moved to PostGIS + admin UI → operators tune in-flight (e.g. raise the "person" floor to 0.5 to silence noisy detections in a busy scene).

## Data sources

- `GET /api/inference/confidence-overrides`
- `PUT /api/inference/confidence-overrides` (admin only)

## Cross-references

- [backend-routers/inference-router.md](../backend-routers/inference-router.md)
- [backend/detection-policy.md](../backend/detection-policy.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
