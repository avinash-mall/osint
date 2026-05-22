# Backend Scripts — Train, Seed, Refmv, Backfill

**Path:** [backend/scripts/](../../backend/scripts/)

| Script | Purpose |
|---|---|
| [train.py](../../backend/scripts/train.py) | Training-job orchestrator. POSTs to `inference-sam3:/train`, polls status, persists metrics. Invoked by `worker.train_model`. |
| [seed_ontology.py](../../backend/scripts/seed_ontology.py) | Idempotent seeder from the bundled defence-ontology JSON. Auto-runs on first boot (empty tables); `--force` to re-seed (destructive). |
| [refmv.py](../../backend/scripts/refmv.py) | One-shot telemetry re-extraction for a clip — deletes existing `fmv_frames`, re-runs `video_metadata.extract_telemetry`. |
| [backfill_detection_branch.py](../../backend/scripts/backfill_detection_branch.py) | Backfill `detections.metadata` with normalized `branch_id`, `icon_key`, `canonical_label`, `ontology_object_id`. Supports `--dry-run`, `--batch-size`, `--where` for safe partial runs. |
| [seeds/](../../backend/scripts/seeds/) | Static seed JSON consumed by `seed_ontology.py` |

## Why these are under `backend/scripts/` not `scripts/`

Anything importing backend modules (`backend.ontology`, `backend.database`, etc.) sits next to them → straightforward import path. Top-level `scripts/` is for stand-alone tooling that doesn't need the backend Python path.

## Usage notes

- `train.py` is **not** for direct invocation in production — the Celery task wraps it. Direct run = debugging.
- `seed_ontology.py --force` is destructive: overwrites the live ontology. Use only on a clean target.
- `backfill_detection_branch.py` is **idempotent and resumable** — always run `--dry-run` first to preview affected rows.

## Cross-references

- [backend-routers/models-training-router.md](../backend-routers/models-training-router.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [backend/video-metadata-klv.md](../backend/video-metadata-klv.md)
