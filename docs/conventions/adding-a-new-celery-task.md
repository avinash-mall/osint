# Recipe — Add a New Celery Task

## When this applies

You need long-running async work the API can't block on: a new ingest type, a periodic cleanup, a training pipeline variant.

## Two placement choices

### A. Add to `backend/worker_legacy.py`

Pragmatic default for tasks sharing helpers with existing pipelines (chip planner, NDJSON consumer, etc.).

```python
# in backend/worker_legacy.py
@celery_app.task(name="worker.process_xyz")
def process_xyz(payload_id: int) -> dict:
    ...
```

Re-export from the facade if there's a natural fit:

```python
# in backend/worker/imagery.py
from worker_legacy import process_xyz  # noqa: F401
```

### B. New module under `backend/`

For tasks that don't fit existing pipelines and would seed a future extraction. Create `backend/<concern>.py` with the task function, then **import it from `worker_legacy.py`** so Celery still discovers it:

```python
# in backend/worker_legacy.py (near top of file or in a registration block)
from <concern> import process_xyz  # noqa: F401  # so Celery sees the task
```

Either way, **`name="worker.xxx"` is the routing identity.** If you ever move the function later, keep the `name=` argument literally identical.

## Required hookups

1. **Queue** — default `default`; long-running things go on `imagery`. Set with `queue="imagery"` on the decorator.
2. **Caller** — some router calls `process_xyz.delay(payload_id)`. The caller should not import the task body — only the `.delay()` interface.
3. **Progress events** — long tasks publish `ingest_progress`-style events. See [backend/events-and-timeline.md](../backend/events-and-timeline.md).
4. **Periodic schedule** — if a beat task, add to the `beat_schedule` block in `worker_legacy.py`. See [operations/celery-beat-schedule.md](../operations/celery-beat-schedule.md).

## Restarts

Celery picks up new tasks on worker restart: `docker compose restart worker worker_beat`.

## Cross-references

- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [backend/worker-package-facade.md](../backend/worker-package-facade.md)
- [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md)
- [operations/celery-queues-and-tasks.md](../operations/celery-queues-and-tasks.md)
