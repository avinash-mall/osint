# Celery Queues & Tasks

**Workers:** `worker` (heavy lifting), `worker_beat` (scheduler)
**Broker:** `redis` ([deployment/docker-compose-services.md](../deployment/docker-compose-services.md))
**Task source:** [backend/worker_legacy.py](../../backend/worker_legacy.py) (re-exported by [worker/__init__.py](../../backend/worker/__init__.py))

## Queues

| Queue | Purpose |
|---|---|
| `imagery` | Long-running imagery ingest, change detection, training |
| `default` | Everything else: FMV ingest, feed polls, cleanup |

Workers are started with `-Q imagery,default` so both queues drain on the same pool by default.

## Key tasks

| Task name | Purpose |
|---|---|
| `worker.process_satellite_imagery` | Full imagery pipeline (COG → chip → /detect → georef → persist) |
| `worker.process_fmv` | Full FMV pipeline (HLS → KLV → /detect_video → persist tracks) |
| `worker.train_model` | Forward training request to inference-sam3, persist results |
| `worker.poll_http_feeds` | Periodic feed poller (Celery beat) |
| `worker.cleanup_old_observations` | Periodic timeline pruning (Celery beat) |

`grep -nE "@celery_app.task" backend/worker_legacy.py` for the live list.

## Retry policy

Most tasks use Celery's default retry (3 attempts, exponential backoff). Imagery ingest with `INFERENCE_CHIP_TIMEOUT_S` will mark individual chips failed but continue with the rest — see [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md).

## Cross-references

- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [backend/worker-package-facade.md](../backend/worker-package-facade.md)
- [conventions/adding-a-new-celery-task.md](../conventions/adding-a-new-celery-task.md)
- [celery-beat-schedule.md](celery-beat-schedule.md)
