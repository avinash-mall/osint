# `backend/worker/` — Package Facade

**Path:** [backend/worker/](../../backend/worker/)
**Files:** `__init__.py`, `_shared.py`, `dispatch.py`, `fmv.py`, `imagery.py`, `postprocess.py`

## Purpose

A thin namespace over [worker_legacy.py](worker-legacy-monolith.md) that preserves the Celery task-name routing while giving new code narrow, intent-named imports.

## Files

| File | Purpose |
|---|---|
| [`__init__.py`](../../backend/worker/__init__.py) | `from worker_legacy import *` — re-exports everything; bootstraps `celery_app`, plus underscore helpers tests import directly |
| [`_shared.py`](../../backend/worker/_shared.py) | Env reads, upload-job DB helpers, progress reporter constants |
| [`dispatch.py`](../../backend/worker/dispatch.py) | SAM3 HTTP-client constants + `chip_to_uint8_rgb` re-export |
| [`imagery.py`](../../backend/worker/imagery.py) | COG/slice/satellite imagery task re-exports |
| [`fmv.py`](../../backend/worker/fmv.py) | FMV consumer re-exports |
| [`postprocess.py`](../../backend/worker/postprocess.py) | Dedup/NMS/candidate-link re-exports |

## Why this design

See [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md). The package lets us:

- Keep `@celery_app.task(name="worker.process_fmv")` literally unchanged.
- Let new callers do `from worker.fmv import process_fmv` (clean intent) without forcing legacy callers to migrate.
- Move tasks out one at a time when there's a clear refactor reason (and tests).

## Failure modes

The package contains no real logic, so failure modes are inherited from `worker_legacy.py`. Don't add logic here — put it in a new module under `backend/` and re-export through the appropriate facade.

## Key symbols

- [`_calibration_tag_for_detection`](../../backend/worker_legacy.py#L572) — re-exported for backend unit tests that pin source-layer calibration behavior.

## Cross-references

- [backend/worker-legacy-monolith.md](worker-legacy-monolith.md)
- [conventions/adding-a-new-celery-task.md](../conventions/adding-a-new-celery-task.md)
- [operations/celery-queues-and-tasks.md](../operations/celery-queues-and-tasks.md)
