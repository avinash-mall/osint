# Admin — Models + Processing

**Paths:**
- [frontend/src/components/admin/ModelsView.tsx](../../frontend/src/components/admin/ModelsView.tsx) (~4748 chars)
- [frontend/src/components/admin/ProcessingView.tsx](../../frontend/src/components/admin/ProcessingView.tsx) (~8926 chars)

## ModelsView

Registered detection models with one-click promotion. Promoting writes a row in `model_history` and updates the active-model pointer.

- `GET /api/models`
- `POST /api/models/{id}/promote`
- `POST /api/models/datasets` — register a training dataset
- `GET /api/models/datasets`

## ProcessingView

Live list of analytics + training Celery jobs. Used to monitor: ingest jobs in flight, training jobs queued/running, periodic feed polls.

- `GET /api/training/jobs`
- `GET /api/analytics/jobs`
- `GET /api/ingest/jobs/{task_id}` (when expanding a row)
- WebSocket: `processing_jobs` topic for live status

## Cross-references

- [backend-routers/models-training-router.md](../backend-routers/models-training-router.md)
- [scripts/backend-scripts-train-and-seed.md](../scripts/backend-scripts-train-and-seed.md)
- [operations/celery-queues-and-tasks.md](../operations/celery-queues-and-tasks.md)
