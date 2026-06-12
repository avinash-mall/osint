# Admin — Models + Processing

**Paths:**
- [frontend/src/components/admin/ModelsView.tsx](../../frontend/src/components/admin/ModelsView.tsx) (~4748 chars)
- [frontend/src/components/admin/ProcessingView.tsx](../../frontend/src/components/admin/ProcessingView.tsx) (~8926 chars)

## ModelsView

Registered detection models with one-click promotion. Promoting writes a `model_history` row, updates the active-model pointer.

- `GET /api/models`
- `POST /api/models/{id}/promote`
- `POST /api/models/datasets` — register a training dataset
- `GET /api/models/datasets`

## ProcessingView

Live list of analytics + training Celery jobs. Monitors: ingest jobs in flight, training jobs queued/running, periodic feed polls.

- `GET /api/training/jobs`
- `GET /api/analytics/jobs`
- `GET /api/ingest/jobs/{task_id}` (when expanding a row)
- WebSocket: `processing_jobs` topic for live status

Status normalisation: analytics jobs are stored as `status='complete'`; training
uses `'completed'`/`'done'`. An `isDoneStatus` helper treats all three as
terminal-success for the progress bar, the colour, and the DONE filter. Job
cards show status only — there is no Map/FMV cross-nav (analytics jobs return a
GeoJSON `result`, not a `detection_id`, and training jobs carry neither).

Progress bars: neither jobs API exposes percent-complete, so running jobs render
an indeterminate striped bar (full-width `repeating-linear-gradient`) instead of
a fabricated determinate value; queued = empty, done = full.

## Cross-references

- [backend-routers/models-training-router.md](../backend-routers/models-training-router.md)
- [scripts/backend-scripts-train-and-seed.md](../scripts/backend-scripts-train-and-seed.md)
- [operations/celery-queues-and-tasks.md](../operations/celery-queues-and-tasks.md)
