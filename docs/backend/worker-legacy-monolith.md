# `backend/worker_legacy.py` — Monolithic Celery Tasks

**Path:** [backend/worker_legacy.py](../../backend/worker_legacy.py)
**Lines:** ~3678 (largest file in the repo)
**Depends on:** Most of the rest of `backend/` plus `celery`, `requests`, `numpy`, `rasterio`, `cv2`

## Purpose

Houses every Celery task that does the heavy lifting: imagery pipeline, FMV pipeline, training-job orchestration, audio transcription, and shared helpers like the chip planner and SAM3 HTTP client.

## Why this file is monolithic

See [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md). Celery task names are routing identity; refactoring is gated on preserving the `name=` argument and adding test coverage for each extracted piece.

## Key task names (by `name=` argument)

| Task name | Purpose |
|---|---|
| `worker.process_satellite_imagery` | Imagery ingest: COG → chip → /detect → georef → persist |
| `worker.process_fmv` | FMV ingest: HLS → KLV → /detect_video → persist tracks |
| `worker.train_model` | Forward training request to `inference-sam3:/train` and persist results |
| `worker.transcribe_audio` | (When enabled) audio → text |
| `worker.poll_http_feeds` | Periodic feed polling (Celery beat) |
| `worker.cleanup_old_observations` | Periodic timeline pruning |

`grep -nE "@celery_app.task" backend/worker_legacy.py` for the full live list.

## Key shared helpers (referenced from elsewhere)

- `chip_to_uint8_rgb` — converts a multispectral chip into the 1008×1008 uint8 RGB that SAM3 wants.
- `chip_plan(...)` — slice a COG into chip windows with overlap; used by the imagery pipeline and by [backend/tests/test_chip_emitter.py](../../backend/tests/test_chip_emitter.py).
- SAM3 HTTP client constants (`INFERENCE_SAM3_URL`, timeouts).
- NDJSON consumer for `/detect_video` (parses streaming response and yields per-frame records).
- [`_calibration_tag_for_detection`](../../backend/worker_legacy.py#L572) — chooses `source_layer` for detector-specific calibration.
- [`FMV_DEFAULT_PROMPTS`](../../backend/worker_legacy.py#L165) — small PCS fallback prompt set (`vehicle,person,building`) when the operator did not provide FMV prompts.

## Inputs / Outputs

Imagery tasks emit per-pass summaries including `candidates_by_layer`, `suppressed_by_nms`, and `suppressed_by_policy` from inference debug counts. The imagery pipeline calibrates raw confidence by `source_layer`, applies [detection-policy.md](detection-policy.md), georeferences OBBs, deduplicates across chips, and persists survivors to PostGIS.

FMV tasks consume `/detect_video` NDJSON. SAM3 and YOLOE entries now preserve `source_layer` in row metadata so downstream review can distinguish tracker families.

## Failure modes

- `/detect` 4xx/5xx per chip increments failed chip counts; the worker continues other chips.
- Detections below the active policy floor are counted in `suppressed_by_policy` and not persisted.
- Missing FMV prompts no longer launch a single `"object"` session; the precision fallback launches the bounded `FMV_DEFAULT_PROMPTS` list.

## Re-export shape

Anything in this file is re-exported by [backend/worker/__init__.py](../../backend/worker/__init__.py) so callers can `from worker import process_fmv`. New code should prefer `from worker.imagery import ...` via the [worker package facade](worker-package-facade.md).

## Cross-references

- [backend/worker-package-facade.md](worker-package-facade.md)
- [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [operations/celery-queues-and-tasks.md](../operations/celery-queues-and-tasks.md)
- [conventions/adding-a-new-celery-task.md](../conventions/adding-a-new-celery-task.md)
