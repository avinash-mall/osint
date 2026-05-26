# Why AMG Detection Classes Use LLM Display Labels

## Decision

For imagery uploaded as `model=yolo26 + prompt_mode=amg` (`enabled_layers=["yoloe_pf"]`), the map's Detection Classes panel may promote the LLM-generated advisory label to the primary display label. The raw model class remains the filtering key and is shown as secondary audit text.

## Why

YOLOE-PF is prompt-free and emits broad LVIS-style labels. They are useful for preserving open-vocabulary detections, but labels such as scene or object fragments can read poorly as analyst-facing class names. When Ava is configured, `GET /api/detections/classes?llm=true` can generate a short label and description from the raw class and counts.

This promotion is deliberately narrow:

- Only class rows where every detection came from image AMG / YOLOE-PF get `label_source="llm_advisory"`.
- Mixed rows and SAM3 / PCS rows keep deterministic labels as primary.
- Category, branch, icon, threat, hide/solo filters, and `det_class` API filters continue to use the raw class / deterministic ontology.
- LLM offline or malformed output falls back to deterministic labels without hiding the row.

## Implementation

`backend/main.py` joins detections to `satellite_passes.metadata` and the original `upload_jobs.metadata` via `upload_id` to compute `amg_image_count` and whether the whole class row is AMG-primary. New imagery processing in `backend/worker_legacy.py` copies `model`, `prompt_mode`, and `enabled_layers` into pass metadata so future reads do not depend only on the upload-job record.

`frontend/src/components/GaiaMap.tsx` stores both `label` and `displayLabel` on `DetectionClassStat`; `frontend/src/components/map/LayerPanel.tsx` renders `displayLabel` as primary only when `labelSource === "llm_advisory"` and keeps the raw class visible underneath.

## Cross-references

- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md)
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [decisions/why-imagery-yoloe-mirrors-fmv.md](why-imagery-yoloe-mirrors-fmv.md)
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md)
