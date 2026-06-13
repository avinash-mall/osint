# Backend Scripts — Train, Seed, Refmv, Backfill

**Path:** [backend/scripts/](../../backend/scripts/)

| Script | Purpose |
|---|---|
| [train.py](../../backend/scripts/train.py) | Training-job orchestrator. POSTs to `inference-sam3:/train`, polls status, persists metrics. Invoked by `worker.train_model`. Optional `--tile` runs chip-aligned preprocessing first (see `prepare_training_tiles.py`). |
| [prepare_training_tiles.py](../../backend/scripts/prepare_training_tiles.py) | Chip-aligned training-tile preprocessor. Tiles a YOLO dataset with the SAME `plan_inference_grid` planner inference uses (chip 1008 / overlap 252), rewrites bbox labels into tile coords, passes ≤chip_size images through unchanged. CPU only. See [chip-aligned-training-tiles.md](../decisions/chip-aligned-training-tiles.md). |
| [stage_mvrsd.py](../../backend/scripts/stage_mvrsd.py) | Stage MVRSD (Military Vehicle Remote Sensing Dataset) into a YOLO dataset under `/data/datasets/mvrsd` from the official demo.zip + community YOLO labels, or an operator drop-in of the full (account-locked) imagery. Converts Pascal-VOC XML → YOLO. |
| [stage_dota_chips.py](../../backend/scripts/stage_dota_chips.py) | Crop DOTA `labels.json` annotations into per-class reference chips; used by the reference-corpora drop-in adapter. |
| [seed_ontology.py](../../backend/scripts/seed_ontology.py) | Idempotent seeder from the bundled defence-ontology JSON. Auto-runs on first boot (empty tables); `--reseed` upserts every branch/object AND prunes objects absent from the JSON (so a wholesale taxonomy revision fully applies); `--check` for a dry-run count compare. |
| [refmv.py](../../backend/scripts/refmv.py) | One-shot telemetry re-extraction for a clip — deletes existing `fmv_frames`, re-runs `video_metadata.extract_telemetry`. |
| [backfill_detection_branch.py](../../backend/scripts/backfill_detection_branch.py) | Backfill `detections.metadata` with normalized `branch_id`, `icon_key`, `canonical_label`, `ontology_object_id`. Supports `--dry-run`, `--batch-size`, `--where` for safe partial runs. |
| [seeds/](../../backend/scripts/seeds/) | Static seed JSON consumed by `seed_ontology.py` |

## Why these are under `backend/scripts/` not `scripts/`

Anything importing backend modules (`backend.ontology`, `backend.database`, etc.) sits next to them → straightforward import path. Top-level `scripts/` is for stand-alone tooling that doesn't need the backend Python path.

## Usage notes

- `train.py` is **not** for direct invocation in production — the Celery task wraps it. Direct run = debugging.
- `seed_ontology.py --reseed` overwrites the live ontology and deletes objects no longer in the JSON. Use for a taxonomy revision; it bumps `ontology_version` so the inference prompt cache refreshes.
- `backfill_detection_branch.py` is **idempotent and resumable** — always run `--dry-run` first to preview affected rows.

## Cross-references

- [backend-routers/models-training-router.md](../backend-routers/models-training-router.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [backend/video-metadata-klv.md](../backend/video-metadata-klv.md)
