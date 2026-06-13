# Admin — Confidence Overrides

**Path:** [frontend/src/components/admin/ConfOverrideView.tsx](../../frontend/src/components/admin/ConfOverrideView.tsx)
**Lines:** ~326

## Purpose

Per-class confidence floor editor. Surfaces the `PER_CLASS_CONFIDENCE_OVERRIDES` JSON map as a table, each row `{class, base, value}`. The **BASE** column is the per-class env floor (`env_per_class_confidence_overrides[class]`, falling back to the global env floor when none is set) — the value inference uses without a DB override; the slider's **VALUE** highlights when it is raised above that per-class base. Edits persist to PostGIS, apply on the next request to inference.

## Why this lives in the UI

`PER_CLASS_CONFIDENCE_OVERRIDES` was originally env-only → a restart for any threshold change. Source moved to PostGIS + admin UI → operators tune in-flight (e.g. raise the "person" floor to 0.5 to silence noisy detections in a busy scene).

Env-sourced rows (`from_env`) cannot be deleted from the UI — they are excluded from the save payload and rebuilt from env config on every load, so the trash button was a silent no-op; it is now disabled with a "set via env — override the value instead" tooltip. Error states pass through [`apiErrorMessage`](../../frontend/src/utils/apiError.ts).

## Data sources

- `GET /api/inference/confidence-overrides`
- `PUT /api/inference/confidence-overrides` (admin only)

## Cross-references

- [backend-routers/inference-router.md](../backend-routers/inference-router.md)
- [backend/detection-policy.md](../backend/detection-policy.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
