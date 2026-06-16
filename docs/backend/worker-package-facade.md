# `backend/worker/` — Worker Package

**Path:** [backend/worker/](../../backend/worker/)
**Files:** `config.py`, `app.py`, `_shared.py`, `dispatch.py`, `postprocess.py`, `graph.py`, `fmv.py`, `maintenance.py`, `imagery.py`, `__init__.py`

## Purpose

The Celery worker code, split by concern out of the former 6.2k-line `worker_legacy.py`
monolith (2026-06-16; see [decisions/why-worker-package-split-2026-06-16.md](../decisions/why-worker-package-split-2026-06-16.md)).
`worker_legacy.py` is now a 13-line compatibility shim ([worker-legacy-monolith.md](worker-legacy-monolith.md)).
Every `@celery_app.task(name="worker.xxx")` routing key is unchanged.

## Modules

Imported by `__init__.py` in dependency order (config → app → leaf helpers → tasks) so
every task decorator runs and the full public surface is re-exported.

| File | ~Lines | Responsibility |
|---|--:|---|
| [`config.py`](../../backend/worker/config.py) | 357 | Foundation: the monolith's import preamble, all env constants, profile resolution, GDAL tuning, per-class valid-fraction loader, `env_int/float/bool`, `_det_to_live_feature`. Re-exported via a `dir()`-based `__all__` so each module inherits it with `from worker.config import *`. |
| [`app.py`](../../backend/worker/app.py) | 84 | The singleton `celery_app`, the beat schedule, and `_reset_db_pool_after_fork` (prefork libpq-pool reset). |
| [`_shared.py`](../../backend/worker/_shared.py) | 111 | Upload-job DB rows, imagery-schema bootstrap, `report_progress`, events import. |
| [`dispatch.py`](../../backend/worker/dispatch.py) | 575 | Chip encode/validation, grid planning (`plan_inference_grid`), SAM3 HTTP client: caps negotiation, raw/multipart POST, and inference-restart retry (`_post_chip_with_restart_retry`, `_wait_for_inference_healthy`, `INFERENCE_RESTART_RETRY_MAX`). |
| [`postprocess.py`](../../backend/worker/postprocess.py) | 496 | Cross-chip NMS / dedupe (`_DetectionDedupeIndex`), weighted-box fusion (`_WeightedBoxFusionIndex`), per-class IoU/trust, geo re-derivation, `_calibration_tag_for_detection`. |
| [`graph.py`](../../backend/worker/graph.py) | 1171 | Phase 2-6 graph tasks: NEAR/colocation/GNN builders, entity proposal/resimilarity, repeat detector, ontology/observation/document/FMV projectors, `_parse_embedding_anchor`. |
| [`fmv.py`](../../backend/worker/fmv.py) | 719 | FMV windowed tracking: `process_fmv`, `consolidate_fmv` ([fmv-track-consolidation.md](fmv-track-consolidation.md)) + window slicing / profile / NDJSON helpers. |
| [`maintenance.py`](../../backend/worker/maintenance.py) | 535 | Periodic + admin tasks: `transcribe_audio`, `train_model`, `tick_collection_scheduler`, `tick_feed_poll`, `cleanup_old_observations`, `seed_reference_db` + reference-DB bake. |
| [`imagery.py`](../../backend/worker/imagery.py) | 2262 | Imagery ingest orchestration: COG conversion, `slice_and_infer`, SAR CFAR, `store_detections`, candidate links, the `process_satellite_imagery` task. |
| [`__init__.py`](../../backend/worker/__init__.py) | 22 | Imports submodules in order; re-exports the full surface (`__all__ = dir()`), so `from worker import X` is unchanged. |

Cross-module wiring (not fully resolvable by the config star-import): `imagery` imports
`dispatch`/`postprocess`/`_shared` and `graph._parse_embedding_anchor`; `fmv` imports `dispatch`
and `graph.project_fmv_to_graph`; `postprocess` imports `geometry.iou_xyxy`; `fmv`/`maintenance`
import `events.publish_event`.

## Testing the split (parity harness)

[`backend/tests/test_worker_api_parity.py`](../../backend/tests/test_worker_api_parity.py) pins,
against a committed baseline ([`_worker_api_baseline.json`](../../backend/tests/_worker_api_baseline.json)),
the exact Celery task-routing-key set and the public import surface — the contracts a no-GPU
refactor must not silently break. The split was also verified with a `LOAD_GLOBAL` disassembly
scan (every moved function + task body resolves its globals without executing it).

## Monkeypatching note

A function resolves its globals in the module where it is **defined**, not in the `worker_legacy`
shim. Tests that monkeypatch a moved helper/constant must target its owning module — e.g.
`import worker.dispatch as worker` then patch `worker._wait_for_inference_healthy`, or
`import worker.graph as worker_legacy` for graph-task tests.

## Failure modes

Import order matters: a module importing a later sibling would break boot — `__init__.py`
fixes the order (graph before fmv). The `dir()`-based `__all__` re-exports inherited names too
(harmless duplication); the parity test guards against any genuine public-API drop.

## Cross-references

- [backend/worker-legacy-monolith.md](worker-legacy-monolith.md)
- [decisions/why-worker-package-split-2026-06-16.md](../decisions/why-worker-package-split-2026-06-16.md)
- [conventions/adding-a-new-celery-task.md](../conventions/adding-a-new-celery-task.md)
- [operations/celery-queues-and-tasks.md](../operations/celery-queues-and-tasks.md)
